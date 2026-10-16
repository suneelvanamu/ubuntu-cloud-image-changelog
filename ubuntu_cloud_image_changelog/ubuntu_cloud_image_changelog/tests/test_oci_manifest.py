import unittest.mock as mock

import oci
import pytest

from ubuntu_cloud_image_changelog import oci_manifest


def _make_object(name):
    obj = mock.MagicMock()
    obj.name = name
    return obj


def _make_list_response(names, next_start_with=None):
    response = mock.MagicMock()
    response.data.objects = [_make_object(name) for name in names]
    response.data.next_start_with = next_start_with
    return response


def test_list_manifests_filters_to_single_arch_and_image_type():
    """A serial directory with multiple arch/image-type variants should
    only yield the ones matching the requested arch and image_type."""
    client = mock.MagicMock()
    client.list_objects.return_value = _make_list_response(
        [
            "noble/platform/20260828/ubuntu-amd64-server-20260828-platform.manifest",
            "noble/platform/20260828/ubuntu-arm64-server-20260828-platform.manifest",
            "noble/platform/20260828/ubuntu-amd64-minimal-20260828-platform.manifest",
            "noble/platform/base/20260908/ubuntu-amd64-server-20260908-platform.manifest",
        ]
    )

    manifests = oci_manifest.list_manifests(
        client, "namespace", "platform-shared", "noble", arch="amd64", image_type="server"
    )

    assert [m.serial for m in manifests] == ["20260828", "20260908"]
    assert all(m.arch == "amd64" and m.image_type == "server" for m in manifests)


def test_list_manifests_paginates():
    """list_manifests should follow next_start_with until it is empty."""
    client = mock.MagicMock()
    first_page = _make_list_response(
        ["noble/platform/20260828/ubuntu-amd64-server-20260828-platform.manifest"],
        next_start_with="token",
    )
    second_page = _make_list_response(["noble/platform/20260908/ubuntu-amd64-server-20260908-platform.manifest"])
    client.list_objects.side_effect = [first_page, second_page]

    manifests = oci_manifest.list_manifests(client, "namespace", "platform-shared", "noble")

    assert [m.serial for m in manifests] == ["20260828", "20260908"]
    assert client.list_objects.call_count == 2
    assert client.list_objects.call_args_list[1].kwargs["start"] == "token"


def test_find_latest_manifests_returns_newest_first():
    client = mock.MagicMock()
    client.list_objects.return_value = _make_list_response(
        [
            "noble/platform/20260828/ubuntu-amd64-server-20260828-platform.manifest",
            "noble/platform/20260908/ubuntu-amd64-server-20260908-platform.manifest",
            "noble/platform/20260818/ubuntu-amd64-server-20260818-platform.manifest",
        ]
    )

    manifests = oci_manifest.find_latest_manifests(client, "namespace", "platform-shared", "noble")

    assert [m.serial for m in manifests] == ["20260908", "20260828"]


def test_find_latest_manifests_raises_when_none_found():
    """No matching manifests for the requested arch/image-type should raise
    a clear error rather than an IndexError downstream."""
    client = mock.MagicMock()
    client.list_objects.return_value = _make_list_response([])

    with pytest.raises(ValueError, match="No 'amd64' 'server' manifests found"):
        oci_manifest.find_latest_manifests(client, "namespace", "platform-shared", "noble")


def test_find_latest_manifests_raises_when_not_enough_found():
    """Only one matching manifest should raise rather than silently
    returning a short, unusable list."""
    client = mock.MagicMock()
    client.list_objects.return_value = _make_list_response(
        ["noble/platform/20260908/ubuntu-amd64-server-20260908-platform.manifest"]
    )

    with pytest.raises(ValueError, match="Found only 1"):
        oci_manifest.find_latest_manifests(client, "namespace", "platform-shared", "noble")


def test_get_namespace_returns_client_data():
    client = mock.MagicMock()
    client.get_namespace.return_value.data = "intcanonical"

    assert oci_manifest.get_namespace(client) == "intcanonical"


def test_get_object_storage_client_uses_config_file_when_given():
    with mock.patch("oci.config.from_file") as mock_from_file, mock.patch(
        "oci.object_storage.ObjectStorageClient"
    ) as mock_client_cls:
        oci_manifest.get_object_storage_client(oci_config_file="/tmp/config", oci_profile="DEFAULT")

    mock_from_file.assert_called_once_with(file_location="/tmp/config", profile_name="DEFAULT")
    mock_client_cls.assert_called_once()


def test_get_object_storage_client_raises_on_auth_failure():
    """A broken/missing OCI config should surface as an exception rather
    than being swallowed."""
    with mock.patch("oci.config.from_file", side_effect=oci.exceptions.ConfigFileNotFound("missing")):
        with pytest.raises(oci.exceptions.ConfigFileNotFound):
            oci_manifest.get_object_storage_client(oci_config_file="/does/not/exist")


def test_download_manifest_writes_streamed_content(tmp_path):
    client = mock.MagicMock()
    response = mock.MagicMock()
    response.data.raw.stream.return_value = [b"chunk-one", b"chunk-two"]
    client.get_object.return_value = response
    manifest = oci_manifest.ManifestObject(
        serial="20260908",
        object_name="noble/platform/20260908/ubuntu-amd64-server-20260908-platform.manifest",
        arch="amd64",
        image_type="server",
    )
    dest_path = str(tmp_path / "downloaded.manifest")

    result = oci_manifest.download_manifest(client, "namespace", "platform-shared", manifest, dest_path)

    assert result == dest_path
    with open(dest_path, "rb") as f:
        assert f.read() == b"chunk-onechunk-two"
    client.get_object.assert_called_once_with("namespace", "platform-shared", manifest.object_name)
