#!/usr/bin/python3
import hashlib
import logging
import os
import requests

# Paper API configuration
PAPER_API_BASE = "https://fill.papermc.io/v3"
USER_AGENT = "PaperUpdater/1.0 (https://github.com/bronson/paperupdater)"
HEADERS = {"User-Agent": USER_AGENT}
LOG_FILENAME = "paper_updater.log"
LOG_FORMAT = "%(asctime)s %(message)s"

# Log to both the log file and the screen
logger = logging.getLogger()
logger.setLevel(logging.INFO)
formatter = logging.Formatter(LOG_FORMAT)

file_handler = logging.FileHandler(LOG_FILENAME)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

def get_latest_stable(project):
    """Find the latest stable build for the given PaperMC project.

    Returns a tuple of (version, build_id, download_url, jar_name).
    """
    logging.info(f"Fetching {project.capitalize()} version list...")
    response = requests.get(f"{PAPER_API_BASE}/projects/{project}", headers=HEADERS)
    response.raise_for_status()

    # .versions is a dict of version groups (e.g. {"1.21": ["1.21.4", "1.21.3", ...], ...}),
    # ordered newest-first. Flatten into a single list to iterate through.
    version_groups = response.json()["versions"]
    all_versions = [v for group in version_groups.values() for v in group]

    for version in all_versions:
        logging.info(f"Checking {version} for stable builds...")
        builds_resp = requests.get(f"{PAPER_API_BASE}/projects/{project}/versions/{version}/builds", headers=HEADERS)
        builds_resp.raise_for_status()
        builds = builds_resp.json()

        stable_builds = [b for b in builds if b.get("channel") == "STABLE"]
        if stable_builds:
            build = stable_builds[0]  # newest stable build is first
            dl = build["downloads"]["server:default"]
            return version, build["id"], dl["url"], dl["name"], dl["checksums"]["sha256"]

    raise RuntimeError(f"No stable builds found for any {project.capitalize()} version.")


def update_symlink(jar_name, symlink):
    """Point symlink at jar_name, replacing whatever currently exists there."""
    if os.path.islink(symlink) and os.readlink(symlink) == jar_name:
        logging.info(f"{symlink} already points to {jar_name}.")
        return
    if os.path.lexists(symlink):
        os.remove(symlink)
    os.symlink(jar_name, symlink)
    logging.info(f"Updated {symlink} -> {jar_name}.")


def verify_checksum(jar_name, expected_sha256):
    """Compute the SHA-256 digest of jar_name and compare it to expected_sha256.

    Raises ValueError if the digests do not match.
    """
    sha256 = hashlib.sha256()
    with open(jar_name, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)
    actual = sha256.hexdigest()
    if actual != expected_sha256:
        raise ValueError(
            f"Checksum mismatch for {jar_name}: "
            f"expected {expected_sha256}, got {actual}"
        )
    logging.info(f"Checksum verified for {jar_name}.")


def download_server(version, build_id, download_url, jar_name, sha256, symlink):
    logging.info(f"Downloading {version} build {build_id}...")
    response = requests.get(download_url, headers=HEADERS)
    response.raise_for_status()
    with open(jar_name, "wb") as jar_file:
        jar_file.write(response.content)
    logging.info(f"Saved to {jar_name}.")
    try:
        verify_checksum(jar_name, sha256)
    except ValueError as e:
        logging.error(str(e))
        os.remove(jar_name)
        raise
    update_symlink(jar_name, symlink)


def main():
    logging.info("--- Starting update run ---")

    if os.path.lexists("velocity.jar"):
        project, symlink = "velocity", "velocity.jar"
    else:
        project, symlink = "paper", "paper.jar"

    logging.info(f"Updating {project.capitalize()}...")
    version, build_id, download_url, jar_name, sha256 = get_latest_stable(project)
    logging.info(f"Latest stable {project.capitalize()} version: {version}, build {build_id}.")

    if os.path.isfile(jar_name):
        logging.info(f"Already have {jar_name} — no update needed.")
        update_symlink(jar_name, symlink)
    else:
        download_server(version, build_id, download_url, jar_name, sha256, symlink)

    logging.info("--- Done ---")


if __name__ == "__main__":
    main()
