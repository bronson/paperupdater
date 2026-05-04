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
    AssetInfo,
    download_file,
    fetch_papermc,
    load_config,
    prune_old_downloads,
    resolve_asset,
    update_symlink,
    verify_checksum,
    version_glob,
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
    assert os.readlink(link) == jar  # absolute


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
    assert os.readlink(link) == new_jar


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
    os.symlink(jar, link)  # absolute, matching what update_symlink creates

    assert update_symlink(jar, link) is False
    assert os.readlink(link) == jar


def test_symlink_replaces_regular_file(tmp_path):
    jar = str(tmp_path / "paper-1.21.4-53.jar")
    link = str(tmp_path / "paper.jar")
    open(jar, "w").close()
    # Someone put a regular file where the symlink should go
    with open(link, "w") as f:
        f.write("not a jar")

    assert update_symlink(jar, link) is True
    assert os.path.islink(link)
    assert os.readlink(link) == jar


def test_symlink_creates_parent_dirs(tmp_path):
    jar = str(tmp_path / "downloads" / "paper-1.21.4-53.jar")
    os.makedirs(tmp_path / "downloads")
    open(jar, "w").close()
    link = str(tmp_path / "searanch" / "plugins" / "squaremap.jar")

    assert update_symlink(jar, link) is True
    assert os.path.islink(link)
    assert os.readlink(link) == jar


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
        asset = resolve_asset("paper")

    assert asset.filename == jar_name
    assert asset.sha256 == FAKE_SHA256


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
        download_file(AssetInfo(jar_name, url, FAKE_SHA256, "paper"))

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
        download_file(AssetInfo(jar_name, "https://example.com/irrelevant", FAKE_SHA256, "paper"))
        mock_get.assert_not_called()


def test_download_file_deletes_on_checksum_mismatch(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    jar_name = "paper-1.21.4-53.jar"
    url = f"https://example.com/{jar_name}"
    bad_sha256 = "0" * 64

    with patch("update.requests.get", side_effect=_fake_download_response()):
        with pytest.raises(ValueError, match="Checksum mismatch"):
            download_file(AssetInfo(jar_name, url, bad_sha256, "paper"))

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
            asset = resolve_asset("discordsrv")
            download_file(asset)

    assert os.path.isfile(f"downloads/{jar_name}")
    assert any("not verified" in r.message for r in caplog.records)


# ── version_glob ─────────────────────────────────────────────────────────────


def test_version_glob_strips_version():
    assert version_glob("paper-1.21.4-53.jar", "1.21.4-53") == "paper-*.jar"


def test_version_glob_multi_part():
    assert version_glob("voicechat-bukkit-2.6.17.jar", "2.6.17") == "voicechat-bukkit-*.jar"


def test_version_glob_returns_none_when_no_version():
    assert version_glob("something.jar", None) is None


def test_version_glob_returns_none_when_version_not_found():
    assert version_glob("something.jar", "9.9.9") is None


# ── prune_old_downloads ──────────────────────────────────────────────────────


def _make_jar(dirpath, name, mtime_offset=0):
    """Create an empty jar file with a specific mtime."""
    p = dirpath / name
    p.write_text("")
    if mtime_offset:
        import time
        os.utime(str(p), (mtime_offset, mtime_offset))
    return str(p)


def test_prune_removes_oldest_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    dl = tmp_path / "downloads"
    dl.mkdir()
    for i, ver in enumerate(["1.0", "2.0", "3.0", "4.0", "5.0"]):
        _make_jar(dl, f"paper-{ver}.jar", mtime_offset=1000 + i)

    prune_old_downloads("paper-*.jar", keep=3)

    remaining = sorted(os.listdir(str(dl)))
    assert remaining == ["paper-3.0.jar", "paper-4.0.jar", "paper-5.0.jar"]


def test_prune_does_nothing_when_at_keep_limit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    dl = tmp_path / "downloads"
    dl.mkdir()
    for i, ver in enumerate(["1.0", "2.0", "3.0"]):
        _make_jar(dl, f"paper-{ver}.jar", mtime_offset=1000 + i)

    prune_old_downloads("paper-*.jar", keep=3)

    remaining = sorted(os.listdir(str(dl)))
    assert remaining == ["paper-1.0.jar", "paper-2.0.jar", "paper-3.0.jar"]


def test_prune_does_nothing_when_below_keep_limit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    dl = tmp_path / "downloads"
    dl.mkdir()
    for i, ver in enumerate(["1.0", "2.0"]):
        _make_jar(dl, f"paper-{ver}.jar", mtime_offset=1000 + i)

    prune_old_downloads("paper-*.jar", keep=5)

    remaining = sorted(os.listdir(str(dl)))
    assert remaining == ["paper-1.0.jar", "paper-2.0.jar"]


def test_prune_does_not_touch_different_prefix(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    dl = tmp_path / "downloads"
    dl.mkdir()
    for i, ver in enumerate(["1.0", "2.0", "3.0", "4.0"]):
        _make_jar(dl, f"paper-{ver}.jar", mtime_offset=1000 + i)
    _make_jar(dl, "velocity-3.5.0.jar", mtime_offset=2000)

    prune_old_downloads("paper-*.jar", keep=2)

    remaining = sorted(os.listdir(str(dl)))
    assert remaining == ["paper-3.0.jar", "paper-4.0.jar", "velocity-3.5.0.jar"]


def test_prune_handles_multi_part_prefix(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    dl = tmp_path / "downloads"
    dl.mkdir()
    for i, ver in enumerate(["2.5", "2.6", "2.7"]):
        _make_jar(dl, f"voicechat-bukkit-{ver}.jar", mtime_offset=1000 + i)
    _make_jar(dl, "voicechat-spigot-2.7.jar", mtime_offset=2000)

    prune_old_downloads("voicechat-bukkit-*.jar", keep=1)

    remaining = sorted(os.listdir(str(dl)))
    assert remaining == ["voicechat-bukkit-2.7.jar", "voicechat-spigot-2.7.jar"]


def test_prune_noop_when_keep_is_zero_or_none(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    dl = tmp_path / "downloads"
    dl.mkdir()
    for ver in ["1.0", "2.0"]:
        _make_jar(dl, f"paper-{ver}.jar")

    prune_old_downloads("paper-*.jar", keep=0)
    prune_old_downloads("paper-*.jar", keep=None)

    assert len(os.listdir(str(dl))) == 2


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
