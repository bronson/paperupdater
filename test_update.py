#!/usr/bin/env python3

"""Tests for update.py

Run with:  pytest test_update.py
       or:  ./test_update.py
"""

import hashlib
import os

import pytest
from unittest.mock import MagicMock, patch

from update import (
    download_file,
    fetch_papermc,
    load_config,
    resolve_asset,
    update_symlink,
    verify_checksum,
)


FAKE_JAR_BYTES = b"fake-jar-bytes"
FAKE_SHA256 = hashlib.sha256(FAKE_JAR_BYTES).hexdigest()


# ── helpers ───────────────────────────────────────────────────────────────────


def fake_papermc_responses(project, version, build_id):
    """Return the mocked requests.get() responses for a PaperMC fetch.
    Also returns the expected jar filename.
    """
    jar_name = f"{project}-{version}-{build_id}.jar"

    versions_resp = MagicMock()
    versions_resp.json.return_value = {"versions": {"stable": [version]}}

    builds_resp = MagicMock()
    builds_resp.json.return_value = [
        {
            "channel": "STABLE",
            "id": build_id,
            "downloads": {
                "server:default": {
                    "url": f"https://example.com/{jar_name}",
                    "name": jar_name,
                    "checksums": {"sha256": FAKE_SHA256},
                }
            },
        }
    ]

    download_resp = MagicMock()
    download_resp.content = FAKE_JAR_BYTES

    return [versions_resp, builds_resp, download_resp], jar_name


def write_config(tmp_path, content):
    """Write a config.py file and return its path."""
    config_path = tmp_path / "config.py"
    config_path.write_text(content)
    return str(config_path)


# ── update_symlink ────────────────────────────────────────────────────────────


def test_symlink_is_created(tmp_path):
    jar = str(tmp_path / "paper-1.21.4-53.jar")
    link = str(tmp_path / "paper.jar")
    open(jar, "w").close()

    assert update_symlink(jar, link) is True
    assert os.path.islink(link)
    assert os.readlink(link) == "paper-1.21.4-53.jar"  # relative


def test_symlink_is_updated_to_new_version(tmp_path):
    old_jar = str(tmp_path / "paper-1.21.3-52.jar")
    new_jar = str(tmp_path / "paper-1.21.4-53.jar")
    link = str(tmp_path / "paper.jar")
    open(old_jar, "w").close()
    open(new_jar, "w").close()
    os.symlink(old_jar, link)
    # Make new_jar appear newer than the symlink
    t = os.lstat(link).st_mtime + 1
    os.utime(new_jar, (t, t))

    assert update_symlink(new_jar, link) is True
    assert os.readlink(link) == "paper-1.21.4-53.jar"


def test_symlink_is_left_alone_when_manually_changed(tmp_path):
    old_jar = str(tmp_path / "paper-1.21.3-52.jar")
    new_jar = str(tmp_path / "paper-1.21.4-53.jar")
    link = str(tmp_path / "paper.jar")
    open(old_jar, "w").close()
    open(new_jar, "w").close()
    os.symlink(old_jar, link)
    # Simulate user re-pointing the symlink after new_jar was downloaded
    t = os.path.getmtime(new_jar) + 1
    os.utime(link, (t, t), follow_symlinks=False)

    assert update_symlink(new_jar, link) is False
    assert os.readlink(link) == old_jar


def test_symlink_unchanged_when_already_current(tmp_path):
    jar = str(tmp_path / "paper-1.21.4-53.jar")
    link = str(tmp_path / "paper.jar")
    open(jar, "w").close()
    os.symlink("paper-1.21.4-53.jar", link)  # relative, matching what update_symlink creates

    assert update_symlink(jar, link) is False
    assert os.readlink(link) == "paper-1.21.4-53.jar"


def test_symlink_replaces_regular_file(tmp_path):
    jar = str(tmp_path / "paper-1.21.4-53.jar")
    link = str(tmp_path / "paper.jar")
    open(jar, "w").close()
    # Someone put a regular file where the symlink should go
    with open(link, "w") as f:
        f.write("not a jar")

    assert update_symlink(jar, link) is True
    assert os.path.islink(link)
    assert os.readlink(link) == "paper-1.21.4-53.jar"


def test_symlink_creates_parent_dirs(tmp_path):
    jar = str(tmp_path / "downloads" / "paper-1.21.4-53.jar")
    os.makedirs(tmp_path / "downloads")
    open(jar, "w").close()
    link = str(tmp_path / "searanch" / "plugins" / "squaremap.jar")

    assert update_symlink(jar, link) is True
    assert os.path.islink(link)
    assert os.readlink(link) == os.path.relpath(jar, str(tmp_path / "searanch" / "plugins"))


# ── config loading ────────────────────────────────────────────────────────────


def test_load_config(tmp_path):
    config_path = write_config(tmp_path, """
restart_hook = "echo hello"
servers = {
    "test": {
        "server": "paper",
        "restart_hook": "echo test",
    },
}
""")
    config = load_config(config_path)
    assert config["restart_hook"] == "echo hello"
    assert "test" in config["servers"]
    assert config["servers"]["test"]["server"] == "paper"


# ── checksum verification ─────────────────────────────────────────────────────


def test_verify_checksum_passes_for_correct_hash(tmp_path):
    jar = tmp_path / "paper-1.21.4-53.jar"
    jar.write_bytes(FAKE_JAR_BYTES)
    verify_checksum(str(jar), FAKE_SHA256)


def test_verify_checksum_raises_for_wrong_hash(tmp_path):
    jar = tmp_path / "paper-1.21.4-53.jar"
    jar.write_bytes(FAKE_JAR_BYTES)
    with pytest.raises(ValueError, match="Checksum mismatch"):
        verify_checksum(str(jar), "0" * 64)


# ── resolve_asset ─────────────────────────────────────────────────────────────


def test_resolve_papermc_asset():
    responses, jar_name = fake_papermc_responses("paper", "1.21.4", 53)
    with patch("update.requests.get", side_effect=responses):
        filename, url, sha256 = resolve_asset("paper")

    assert filename == jar_name
    assert sha256 == FAKE_SHA256


def test_resolve_unknown_asset_raises():
    with pytest.raises(ValueError, match="Unknown asset"):
        resolve_asset("nonexistent_plugin")


# ── download_file ─────────────────────────────────────────────────────────────


def _fake_download_response():
    """Return a single mock response for a direct download."""
    resp = MagicMock()
    resp.content = FAKE_JAR_BYTES
    return [resp]


def test_download_file_saves_and_verifies(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    jar_name = "paper-1.21.4-53.jar"
    url = f"https://example.com/{jar_name}"

    with patch("update.requests.get", side_effect=_fake_download_response()):
        download_file(jar_name, url, FAKE_SHA256)

    assert os.path.isfile(f"downloads/{jar_name}")
    assert open(f"downloads/{jar_name}", "rb").read() == FAKE_JAR_BYTES


def test_download_file_skips_if_already_present(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs("downloads")
    jar_name = "paper-1.21.4-53.jar"
    filepath = f"downloads/{jar_name}"
    with open(filepath, "wb") as f:
        f.write(FAKE_JAR_BYTES)

    # Should not fetch anything
    with patch("update.requests.get") as mock_get:
        download_file(jar_name, "https://example.com/irrelevant", FAKE_SHA256)
        mock_get.assert_not_called()


def test_download_file_deletes_on_checksum_mismatch(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    jar_name = "paper-1.21.4-53.jar"
    url = f"https://example.com/{jar_name}"
    bad_sha256 = "0" * 64

    with patch("update.requests.get", side_effect=_fake_download_response()):
        with pytest.raises(ValueError, match="Checksum mismatch"):
            download_file(jar_name, url, bad_sha256)

    assert not os.path.isfile(f"downloads/{jar_name}")


def test_download_file_errors_when_checksum_required_but_missing(tmp_path, monkeypatch):
    """If PaperMC doesn't return a checksum, the fetcher itself should raise."""
    versions_resp = MagicMock()
    versions_resp.json.return_value = {"versions": {"stable": ["1.21.4"]}}

    builds_resp = MagicMock()
    builds_resp.json.return_value = [
        {
            "channel": "STABLE",
            "id": "53",
            "downloads": {
                "server:default": {
                    "url": "https://example.com/paper-1.21.4-53.jar",
                    "name": "paper-1.21.4-53.jar",
                    "checksums": {},  # no sha256!
                }
            },
        }
    ]

    with patch("update.requests.get", side_effect=[versions_resp, builds_resp]):
        with pytest.raises(RuntimeError, match="Downgrade attack suspected"):
            resolve_asset("paper")


def test_download_file_warns_when_checksum_optional_and_missing(tmp_path, monkeypatch, caplog):
    """GitHub releases don't provide checksums — download should proceed with a warning."""
    import logging
    monkeypatch.chdir(tmp_path)
    jar_name = "discordsrv-1.30.5.jar"

    release_resp = MagicMock()
    release_resp.json.return_value = {
        "assets": [{"name": jar_name, "browser_download_url": f"https://example.com/{jar_name}"}],
    }
    download_resp = MagicMock()
    download_resp.content = FAKE_JAR_BYTES

    with patch("update.requests.get", side_effect=[release_resp, download_resp]):
        with caplog.at_level(logging.WARNING):
            filename, url, sha256 = resolve_asset("discordsrv")
            download_file(filename, url, sha256)

    assert os.path.isfile(f"downloads/{jar_name}")
    assert any("not verified" in r.message for r in caplog.records)


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
