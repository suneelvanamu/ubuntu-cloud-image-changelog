"""Helpers for discovering and downloading Ubuntu cloud image manifests from OCI Object Storage.

This module automates the manual steps proven during the CPC-3345 spike:
find the latest two promoted manifests for a given Ubuntu series in the
``platform-shared`` bucket, then download them so they can be fed straight
into :func:`ubuntu_cloud_image_changelog.cli.generate`.

Manifest object names have been observed in two coexisting forms in
``platform-shared``:

* legacy: ``{suite}/platform/{serial}/ubuntu-...-platform.manifest``
* current: ``{suite}/platform/base/{serial}/ubuntu-...-platform.manifest``

Both are matched so callers don't need to know which form is in use for a
given suite/time period.
"""

import re
from typing import List, NamedTuple, Optional

import oci

# Matches both the legacy "{suite}/platform/{serial}/..." and the newer
# "{suite}/platform/base/{serial}/..." manifest object naming conventions.
# Each serial directory can contain more than one manifest - one per
# architecture (amd64/arm64) and image type (server/minimal/etc) - so both
# are captured to allow filtering down to a single, comparable manifest per
# serial.
MANIFEST_OBJECT_NAME_RE_TEMPLATE = (
    r"^{suite}/platform/(?:base/)?(?P<serial>\d{{8}})/"
    r"ubuntu-(?P<arch>[a-z0-9]+)-(?P<image_type>[a-z]+)-[^/]*-platform\.manifest$"
)


class ManifestObject(NamedTuple):
    """A single manifest found in an OCI Object Storage bucket."""

    serial: str
    object_name: str
    arch: str
    image_type: str


def get_object_storage_client(
    oci_config_file: Optional[str] = None,
    oci_profile: str = "DEFAULT",
) -> oci.object_storage.ObjectStorageClient:
    """Build an OCI ObjectStorageClient from a config file.

    :param oci_config_file: Path to an OCI config file. Defaults to the
        standard ``~/.oci/config`` location used by the OCI CLI/SDK when
        ``None``.
    :param oci_profile: The profile within the config file to use.
    """
    config = (
        oci.config.from_file(file_location=oci_config_file, profile_name=oci_profile)
        if oci_config_file
        else (oci.config.from_file(profile_name=oci_profile))
    )
    return oci.object_storage.ObjectStorageClient(config)


def get_namespace(object_storage_client: oci.object_storage.ObjectStorageClient) -> str:
    """Return the Object Storage namespace for the tenancy associated with this client."""
    return object_storage_client.get_namespace().data


def list_manifests(
    object_storage_client: oci.object_storage.ObjectStorageClient,
    namespace: str,
    bucket: str,
    suite: str,
    arch: str = "amd64",
    image_type: str = "server",
) -> List[ManifestObject]:
    """List platform manifests for a suite/arch/image-type in a bucket.

    A serial directory typically contains several manifests - one per
    architecture and image type (eg. amd64/server, arm64/minimal) - so
    results are filtered down to a single, directly comparable manifest per
    serial matching ``arch`` and ``image_type``. Not every serial publishes
    every combination (eg. some serials are "minimal" only), so some serials
    may be absent from the result.

    Results are not sorted; use :func:`find_latest_manifests` to get the
    most recent manifests in order.
    """
    manifest_object_name_re = re.compile(MANIFEST_OBJECT_NAME_RE_TEMPLATE.format(suite=re.escape(suite)))
    manifests = []
    next_start_with = None
    while True:
        response = object_storage_client.list_objects(
            namespace,
            bucket,
            prefix="{}/platform/".format(suite),
            start=next_start_with,
        )
        for obj in response.data.objects:
            match = manifest_object_name_re.match(obj.name)
            if match and match.group("arch") == arch and match.group("image_type") == image_type:
                manifests.append(
                    ManifestObject(
                        serial=match.group("serial"),
                        object_name=obj.name,
                        arch=match.group("arch"),
                        image_type=match.group("image_type"),
                    )
                )
        next_start_with = response.data.next_start_with
        if not next_start_with:
            break
    return manifests


def find_latest_manifests(
    object_storage_client: oci.object_storage.ObjectStorageClient,
    namespace: str,
    bucket: str,
    suite: str,
    arch: str = "amd64",
    image_type: str = "server",
    count: int = 2,
) -> List[ManifestObject]:
    """Return the ``count`` most recently promoted manifests for a suite, newest first.

    "Published" is defined as the latest manifest(s) actually present in the
    promotion bucket (``platform-shared`` in production) rather than Oracle's
    public catalog, since there is no reliable, API-traceable way to map an
    Oracle catalog listing back to our build serial (see CPC-3345 spike
    findings - Oracle's own publish latency is ~11 days with no shared
    identifier).
    """
    manifests = list_manifests(object_storage_client, namespace, bucket, suite, arch=arch, image_type=image_type)
    if not manifests:
        raise ValueError(
            "No '{}' '{}' manifests found for suite '{}' in bucket '{}'".format(arch, image_type, suite, bucket)
        )
    manifests.sort(key=lambda m: m.serial, reverse=True)
    if len(manifests) < count:
        raise ValueError(
            "Found only {} '{}' '{}' manifest(s) for suite '{}' in bucket '{}', need at least {}".format(
                len(manifests), arch, image_type, suite, bucket, count
            )
        )
    return manifests[:count]


def download_manifest(
    object_storage_client: oci.object_storage.ObjectStorageClient,
    namespace: str,
    bucket: str,
    manifest: ManifestObject,
    dest_path: str,
) -> str:
    """Download a manifest object to ``dest_path`` and return the path."""
    response = object_storage_client.get_object(namespace, bucket, manifest.object_name)
    with open(dest_path, "wb") as dest_file:
        for chunk in response.data.raw.stream(1024 * 1024, decode_content=False):
            dest_file.write(chunk)
    return dest_path
