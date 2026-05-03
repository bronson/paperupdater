#!/usr/bin/python3
import hashlib
import logging
import os
import time
import requests

# Paper API configuration
PAPER_API_BASE = "https://fill.papermc.io/v3"
USER_AGENT = "PaperUpdater/1.0 (https://github.com/bronson/paperupdater)"
HEADERS = {"User-Agent": USER_AGENT}
LOG_FILENAME = "paper_updater.log"
LOG_FORMAT = "%(asctime)s %(message)s"


def _human_size(bytes_count):
    """Return a human-readable byte count with appropriate unit."""
    if bytes_count >= 1_000_000:
        return f"{bytes_count / 1_000_000:.1f} MB"
    return f"{bytes_count / 1_000:.1f} kB"


def _human_rate(bytes_count, elapsed):
    """Return a human-readable transfer rate."""
    if elapsed <= 0:
        return "∞"
    rate = bytes_count / elapsed
    if rate >= 1_000_000:
        return f"{rate / 1_000_000:.1f} MB/sec"
    return f"{rate / 1_000:.1f} kB/sec"


def fetch(url):
    """Fetch a URL, logging the request, size, duration, and rate."""
    logging.info(f"Fetching {url}")
    start = time.monotonic()
    response = requests.get(url, headers=HEADERS)
    elapsed = time.monotonic() - start
    response.raise_for_status()
    size = len(response.content)
    logging.info(f"Received {_human_size(size)} in {elapsed:.2f}s ({_human_rate(size, elapsed)})")
    return response


def setup_logging():
    """Configure logging to both the log file and the screen."""
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
    response = fetch(f"{PAPER_API_BASE}/projects/{project}")

    # .versions is a dict of version groups (e.g. {"1.21": ["1.21.4", "1.21.3", ...], ...}),
    # ordered newest-first. Flatten into a single list to iterate through.
    version_groups = response.json()["versions"]
    all_versions = [v for group in version_groups.values() for v in group]

    for version in all_versions:
        builds_resp = fetch(f"{PAPER_API_BASE}/projects/{project}/versions/{version}/builds")
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
    if os.path.lexists(symlink) and os.lstat(symlink).st_mtime > os.path.getmtime(jar_name):
        logging.info(f"{symlink} appears to have been manually changed — leaving it alone.")
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
    response = fetch(download_url)
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
    setup_logging()
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
