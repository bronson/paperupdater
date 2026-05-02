#!/usr/bin/env python3

"""Tests for update.py

Run with:  pytest test_update.py
       or:  ./test_update.py
"""

import hashlib
import os

import pytest
from unittest.mock import MagicMock, patch

from update import download_server, get_latest_stable, update_symlink, verify_checksum


FAKE_JAR_BYTES = b"fake-jar-bytes"
FAKE_SHA256 = hashlib.sha256(FAKE_JAR_BYTES).hexdigest()


# ── helpers ───────────────────────────────────────────────────────────────────


def fake_api_responses(project, version, build_id):
    """Return the three mocked requests.get() responses needed for one full
    download run: versions list → builds list → jar download.

    Also returns the expected jar filename so tests can assert on it.
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


# ── update_symlink ────────────────────────────────────────────────────────────


def test_symlink_is_created(tmp_path):
    jar = str(tmp_path / "paper-1.21.4-53.jar")
    link = str(tmp_path / "paper.jar")
    open(jar, "w").close()

    update_symlink(jar, link)

    assert os.path.islink(link)
    assert os.readlink(link) == jar


def test_symlink_is_updated_to_new_version(tmp_path):
    old_jar = str(tmp_path / "paper-1.21.3-52.jar")
    new_jar = str(tmp_path / "paper-1.21.4-53.jar")
    link = str(tmp_path / "paper.jar")
    open(old_jar, "w").close()
    open(new_jar, "w").close()
    os.symlink(old_jar, link)

    update_symlink(new_jar, link)

    assert os.readlink(link) == new_jar


def test_symlink_unchanged_when_already_current(tmp_path):
    jar = str(tmp_path / "paper-1.21.4-53.jar")
    link = str(tmp_path / "paper.jar")
    open(jar, "w").close()
    os.symlink(jar, link)

    update_symlink(jar, link)  # should be a no-op

    assert os.readlink(link) == jar  # still points to the same jar


# ── paper & velocity downloads ────────────────────────────────────────────────


def test_paper_is_downloaded_and_symlinked(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    responses, jar_name = fake_api_responses("paper", "1.21.4", 53)

    with patch("update.requests.get", side_effect=responses):
        version, build_id, url, name, sha256 = get_latest_stable("paper")
        download_server(version, build_id, url, name, sha256, "paper.jar")

    assert os.path.isfile(jar_name), "jar file should exist on disk"
    assert open(jar_name, "rb").read() == FAKE_JAR_BYTES
    assert os.path.islink("paper.jar"), "paper.jar should be a symlink"
    assert os.readlink("paper.jar") == jar_name


def test_velocity_is_downloaded_and_symlinked(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    responses, jar_name = fake_api_responses("velocity", "3.5.0-SNAPSHOT", 594)

    with patch("update.requests.get", side_effect=responses):
        version, build_id, url, name, sha256 = get_latest_stable("velocity")
        download_server(version, build_id, url, name, sha256, "velocity.jar")

    assert os.path.isfile(jar_name), "jar file should exist on disk"
    assert open(jar_name, "rb").read() == FAKE_JAR_BYTES
    assert os.path.islink("velocity.jar"), "velocity.jar should be a symlink"
    assert os.readlink("velocity.jar") == jar_name


# ── checksum verification ─────────────────────────────────────────────────────


def test_verify_checksum_passes_for_correct_hash(tmp_path):
    jar = tmp_path / "paper-1.21.4-53.jar"
    jar.write_bytes(FAKE_JAR_BYTES)
    verify_checksum(str(jar), FAKE_SHA256)  # should not raise


def test_verify_checksum_raises_for_wrong_hash(tmp_path):
    jar = tmp_path / "paper-1.21.4-53.jar"
    jar.write_bytes(FAKE_JAR_BYTES)
    bad_sha256 = "0" * 64
    with pytest.raises(ValueError, match="Checksum mismatch"):
        verify_checksum(str(jar), bad_sha256)


def test_bad_checksum_deletes_jar_and_skips_symlink(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    responses, jar_name = fake_api_responses("paper", "1.21.4", 53)
    bad_sha256 = "0" * 64

    with patch("update.requests.get", side_effect=responses):
        version, build_id, url, name, _ = get_latest_stable("paper")
        with pytest.raises(ValueError, match="Checksum mismatch"):
            download_server(version, build_id, url, name, bad_sha256, "paper.jar")

    assert not os.path.isfile(jar_name), "corrupted jar should be deleted"
    assert not os.path.islink("paper.jar"), "symlink should not be created"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
