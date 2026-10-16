import unittest.mock as mock

from click.testing import CliRunner

from ubuntu_cloud_image_changelog import oci_manifest
from ubuntu_cloud_image_changelog.cli import generate_from_oci


def _make_manifest(serial):
    return oci_manifest.ManifestObject(
        serial=serial,
        object_name="noble/platform/{}/ubuntu-amd64-server-{}-platform.manifest".format(serial, serial),
        arch="amd64",
        image_type="server",
    )


def test_generate_from_oci_invokes_generate_with_discovered_serials():
    """The CLI command should discover the latest two manifests, download
    them, and hand them off to the existing `generate` command untouched."""
    to_manifest_object = _make_manifest("20260908")
    from_manifest_object = _make_manifest("20260828")

    def _fake_download(object_storage_client, namespace, bucket, manifest, dest_path):
        # download_manifest normally writes the manifest to disk; create an
        # empty file so the real `open()` calls in generate_from_oci succeed.
        open(dest_path, "wb").close()
        return dest_path

    with mock.patch(
        "ubuntu_cloud_image_changelog.cli.oci_manifest.get_object_storage_client"
    ) as mock_get_client, mock.patch(
        "ubuntu_cloud_image_changelog.cli.oci_manifest.find_latest_manifests",
        return_value=(to_manifest_object, from_manifest_object),
    ) as mock_find_latest, mock.patch(
        "ubuntu_cloud_image_changelog.cli.oci_manifest.download_manifest",
        side_effect=_fake_download,
    ) as mock_download, mock.patch(
        "ubuntu_cloud_image_changelog.cli.generate"
    ) as mock_generate:
        mock_get_client.return_value = mock.MagicMock()

        runner = CliRunner()
        result = runner.invoke(
            generate_from_oci,
            ["--series", "noble"],
        )

    assert result.exit_code == 0, result.output
    mock_find_latest.assert_called_once()
    assert mock_find_latest.call_args.kwargs["arch"] == "amd64"
    assert mock_find_latest.call_args.kwargs["image_type"] == "server"
    assert mock_find_latest.call_args.kwargs["count"] == 2
    assert mock_download.call_count == 2
    # ctx.invoke calls the (mocked) generate callback directly with the
    # discovered serials and downloaded manifest file handles
    mock_generate.assert_called_once()
    call_kwargs = mock_generate.call_args.kwargs
    assert call_kwargs["from_serial"] == "20260828"
    assert call_kwargs["to_serial"] == "20260908"
    assert call_kwargs["from_series"] == "noble"
    assert call_kwargs["to_series"] == "noble"


def test_generate_from_oci_surfaces_missing_manifests_as_clean_error():
    """If find_latest_manifests can't find enough manifests, the CLI should
    exit non-zero with a readable message, not a raw traceback."""
    with mock.patch("ubuntu_cloud_image_changelog.cli.oci_manifest.get_object_storage_client"), mock.patch(
        "ubuntu_cloud_image_changelog.cli.oci_manifest.find_latest_manifests",
        side_effect=ValueError("No 'amd64' 'server' manifests found for suite 'noble' in bucket 'platform-shared'"),
    ):
        runner = CliRunner()
        result = runner.invoke(generate_from_oci, ["--series", "noble"])

    assert result.exit_code != 0
    assert "No 'amd64' 'server' manifests found" in result.output
    assert "Traceback" not in result.output


def test_generate_from_oci_surfaces_auth_failure_as_clean_error():
    """A broken/missing OCI config should produce a clean CLI error rather
    than an unhandled exception."""
    import oci

    with mock.patch(
        "ubuntu_cloud_image_changelog.cli.oci_manifest.get_object_storage_client",
        side_effect=oci.exceptions.ConfigFileNotFound("missing config"),
    ):
        runner = CliRunner()
        result = runner.invoke(generate_from_oci, ["--series", "noble"])

    assert result.exit_code != 0
    assert "Unable to build an OCI Object Storage client" in result.output
    assert "Traceback" not in result.output
