#!/usr/bin/python3
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

def get_latest_stable():
    """Find the latest Paper version that has a stable build.

    Returns a tuple of (version, build_id, download_url, jar_name).
    """
    logging.info("Fetching Paper version list...")
    response = requests.get(f"{PAPER_API_BASE}/projects/paper", headers=HEADERS)
    response.raise_for_status()

    # .versions is a dict of version groups (e.g. {"1.21": ["1.21.4", "1.21.3", ...], ...}),
    # ordered newest-first. Flatten into a single list to iterate through.
    version_groups = response.json()["versions"]
    all_versions = [v for group in version_groups.values() for v in group]

    for version in all_versions:
        logging.info(f"Checking {version} for stable builds...")
        builds_resp = requests.get(f"{PAPER_API_BASE}/projects/paper/versions/{version}/builds", headers=HEADERS)
        builds_resp.raise_for_status()
        builds = builds_resp.json()

        stable_builds = [b for b in builds if b.get("channel") == "STABLE"]
        if stable_builds:
            build = stable_builds[0]  # newest stable build is first
            dl = build["downloads"]["server:default"]
            return version, build["id"], dl["url"], dl["name"]

    raise RuntimeError("No stable builds found for any Paper version.")


def download_server(version, build_id, download_url, jar_name):
    logging.info(f"Downloading Paper {version} build {build_id}...")
    response = requests.get(download_url, headers=HEADERS)
    response.raise_for_status()
    with open(jar_name, "wb") as jar_file:
        jar_file.write(response.content)
    logging.info(f"Saved to {jar_name}.")


def main():
    logging.info("--- Starting update run ---")

    version, build_id, download_url, jar_name = get_latest_stable()
    logging.info(f"Latest stable Paper version: {version}, build {build_id}.")

    if os.path.isfile(jar_name):
        logging.info(f"Already have {jar_name} — no update needed.")
    else:
        download_server(version, build_id, download_url, jar_name)

    logging.info("--- Done ---")


if __name__ == "__main__":
    main()
