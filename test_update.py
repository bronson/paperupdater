"""Tests for update.py

Run with:  pytest test_update.py
"""

import os
from unittest.mock import MagicMock, patch

from update import download_server, get_latest_stable, update_symlink


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
                }
            },
        }
    ]

    download_resp = MagicMock()
    download_resp.content = b"fake-jar-bytes"

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
        version, build_id, url, name = get_latest_stable("paper")
        download_server(version, build_id, url, name, "paper.jar")

    assert os.path.isfile(jar_name), "jar file should exist on disk"
    assert open(jar_name, "rb").read() == b"fake-jar-bytes"
    assert os.path.islink("paper.jar"), "paper.jar should be a symlink"
    assert os.readlink("paper.jar") == jar_name


def test_velocity_is_downloaded_and_symlinked(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    responses, jar_name = fake_api_responses("velocity", "3.5.0-SNAPSHOT", 594)

    with patch("update.requests.get", side_effect=responses):
        version, build_id, url, name = get_latest_stable("velocity")
        download_server(version, build_id, url, name, "velocity.jar")

    assert os.path.isfile(jar_name), "jar file should exist on disk"
    assert open(jar_name, "rb").read() == b"fake-jar-bytes"
    assert os.path.islink("velocity.jar"), "velocity.jar should be a symlink"
    assert os.readlink("velocity.jar") == jar_name
