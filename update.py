#!/usr/bin/env python3
"""Paper Updater — Multi-server and plugin updater for Minecraft."""

import email.header
import hashlib
import logging
import os
import subprocess
import sys
import time

import requests

# ── Constants ─────────────────────────────────────────────────────────────────

USER_AGENT = "PaperUpdater/2.0 (https://github.com/bronson/paperupdater)"
HEADERS = {"User-Agent": USER_AGENT}
LOG_FILENAME = "paper_updater.log"
LOG_FORMAT = "%(asctime)s %(message)s"
DOWNLOADS_DIR = "downloads"

PAPER_API_BASE = "https://fill.papermc.io/v3"
HANGAR_API_BASE = "https://hangar.papermc.io/api/v1"
GITHUB_API_BASE = "https://api.github.com"

# ── Asset Registry ────────────────────────────────────────────────────────────
# Each entry maps an asset name to its source details.
# Inferral: "project" defaults to the asset name.

SERVER_PLATFORMS = {
    "paper": "PAPER",
    "velocity": "VELOCITY",
    "waterfall": "WATERFALL",
}

ASSETS = {
    # Servers
    "paper":    {"source": "papermc"},
    "velocity": {"source": "papermc"},
    # Plugins
    "geyser":            {"source": "hangar",  "project": "GeyserMC/Geyser"},
    "floodgate":         {"source": "hangar",  "project": "GeyserMC/Floodgate"},
    "squaremap":         {"source": "hangar",  "project": "OskarStark/squaremap"},
    "simple-voice-chat": {"source": "hangar",  "project": "henkelmax/SimpleVoiceChat"},
    "discordsrv":        {"source": "github",  "project": "DiscordSRV/DiscordSRV"},
}

# ── Utilities ─────────────────────────────────────────────────────────────────


def _human_size(bytes_count):
    if bytes_count >= 1_000_000:
        return f"{bytes_count / 1_000_000:.1f} MB"
    return f"{bytes_count / 1_000:.1f} kB"


def _human_rate(bytes_count, elapsed):
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
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(LOG_FORMAT)

    file_handler = logging.FileHandler(LOG_FILENAME)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)


def verify_checksum(filepath, expected_sha256):
    """Compute SHA-256 of filepath and compare. Raises ValueError on mismatch."""
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)
    actual = sha256.hexdigest()
    if actual != expected_sha256:
        raise ValueError(
            f"Checksum mismatch for {filepath}: "
            f"expected {expected_sha256}, got {actual}"
        )
    logging.info(f"Checksum verified for {filepath}.")


# ── Fetchers ──────────────────────────────────────────────────────────────────
# Each fetcher takes a config dict and returns (filename, download_url, sha256).
# The filename must uniquely identify that asset, so version at a minimum, and also any variants.
# For example: `floodgate-velocity-2.2.5.jar` is good, `floodgate-latest.jar` is bad.
# sha256 is None when the source doesn't provide checksums (e.g. GitHub).
# If the source *should* provide a checksum but didn't, the fetcher raises an error
# (downgrade attack protection).
# To add a fetcher, define a new function and add it to FETCHERS.


def fetch_papermc(conf):
    """Resolve latest stable build from PaperMC API."""
    project = conf["project"]
    response = fetch(f"{PAPER_API_BASE}/projects/{project}")

    version_groups = response.json()["versions"]
    all_versions = [v for group in version_groups.values() for v in group]

    for version in all_versions:
        builds_resp = fetch(f"{PAPER_API_BASE}/projects/{project}/versions/{version}/builds")
        builds = builds_resp.json()

        stable_builds = [b for b in builds if b.get("channel") == "STABLE"]
        if stable_builds:
            build = stable_builds[0]
            dl = build["downloads"]["server:default"]
            checksums = dl.get("checksums", {})
            sha256 = checksums.get("sha256")
            if not sha256:
                raise RuntimeError(
                    f"Downgrade attack suspected: PaperMC did not provide a checksum "
                    f"for {project} v{version} build {build['id']}"
                )
            return dl["name"], dl["url"], sha256

    raise RuntimeError(f"No stable builds found for {project}.")


def fetch_hangar(conf):
    """Resolve latest release from Hangar API."""
    project = conf["project"]
    resp = fetch(f"{HANGAR_API_BASE}/projects/{project}/versions?limit=1&offset=0")
    result = resp.json().get("result", [])
    if not result:
        raise RuntimeError(f"No versions found for {project} on Hangar.")

    version = result[0]
    downloads = version.get("downloads", {})
    requested_platform = conf.get("platform")

    # Try to find a Hangar-hosted download (has downloadUrl + fileInfo)
    # Prefer the requested platform, then fall back to any
    ordered = list(downloads.items())
    if requested_platform and requested_platform in downloads:
        ordered = [(requested_platform, downloads[requested_platform])] + [
            (k, v) for k, v in ordered if k != requested_platform
        ]

    for platform, dl in ordered:
        if dl.get("downloadUrl") and dl.get("fileInfo"):
            fi = dl["fileInfo"]
            sha256 = fi.get("sha256Hash")
            if not sha256:
                raise RuntimeError(
                    f"Downgrade attack suspected: Hangar did not provide a checksum "
                    f"for {project} {version['name']} ({platform})"
                )
            return fi["name"], dl["downloadUrl"], sha256

    # Fall back to external URL (no checksum available)
    for platform, dl in ordered:
        if dl.get("externalUrl"):
            url = dl["externalUrl"]
            # Get the filename from the Content-Disposition header
            head = requests.head(url, headers=HEADERS, allow_redirects=True)
            cd = head.headers.get("Content-Disposition", "")
            filename = version["name"] + ".jar"
            if 'filename="' in cd:
                raw = cd.split('filename="')[1].split('"')[0]
                # Decode RFC 2047 encoding (e.g. =?UTF-8?Q?floodgate-spigot.jar?=)
                decoded_parts = email.header.decode_header(raw)
                filename = ''.join(
                    part.decode(enc or 'utf-8') if isinstance(part, bytes) else part
                    for part, enc in decoded_parts
                )
            # Need to extract version from the final redirect URL
            # (e.g. /versions/2.9.6/builds/1132/downloads/spigot)
            ver = version["name"]
            parts = head.url.split("/")
            for i, part in enumerate(parts):
                if part == "versions" and i + 1 < len(parts):
                    ver = parts[i + 1]
                    break
            base, ext = os.path.splitext(filename)
            filename = f"{base}-{ver}{ext}"
            logging.warning(
                f"External download for {project} — no checksum available. "
                f"Proceeding without verification for {filename}"
            )
            return filename, url, None

    raise RuntimeError(f"No downloadable files for {project} on Hangar.")


def fetch_github(conf):
    """Resolve latest release from GitHub."""
    repo = conf["project"]  # "owner/repo"
    resp = fetch(f"{GITHUB_API_BASE}/repos/{repo}/releases/latest")
    release = resp.json()

    jar_assets = [a for a in release.get("assets", []) if a["name"].endswith(".jar")]
    if not jar_assets:
        raise RuntimeError(f"No .jar assets found in latest release for {repo}")

    asset = jar_assets[0]
    logging.warning(f"No checksum available for {asset['name']} (GitHub).")
    return asset["name"], asset["browser_download_url"], None


FETCHERS = {
    "papermc": fetch_papermc,
    "hangar":  fetch_hangar,
    "github":  fetch_github,
}


# ── Config ────────────────────────────────────────────────────────────────────


def load_config(path="update.conf"):
    """Load a Python config file. Returns its globals as a dict."""
    config = {}
    with open(path) as f:
        exec(f.read(), config)  # noqa: S102
    return config


# ── Download ──────────────────────────────────────────────────────────────────


def download_file(filename, download_url, sha256):
    """Download a file into DOWNLOADS_DIR, verifying checksum if available.

    sha256 is None when the source doesn't provide checksums (download proceeds
    with a warning). If a checksum is provided and doesn't match, the file is
    deleted and ValueError is raised.
    """
    filepath = os.path.join(DOWNLOADS_DIR, filename)

    # Already downloaded and verified?
    if os.path.isfile(filepath):
        if sha256:
            try:
                verify_checksum(filepath, sha256)
                logging.info(f"Already have {filename} — no download needed.")
                return
            except ValueError:
                logging.warning(f"{filename} exists but checksum mismatch — re-downloading.")
                os.remove(filepath)
        else:
            logging.info(f"Already have {filename} — no checksum to verify, assuming correct.")
            return

    # Download
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    response = fetch(download_url)
    with open(filepath, "wb") as f:
        f.write(response.content)
    logging.info(f"Saved to {filepath}.")

    # Verify
    if sha256:
        try:
            verify_checksum(filepath, sha256)
        except ValueError as e:
            os.remove(filepath)
            raise
    else:
        logging.warning(f"No checksum available for {filename} — download not verified.")


# ── Symlinks ──────────────────────────────────────────────────────────────────


def update_symlink(target, link_path):
    """Point link_path at target via relative symlink. Returns True if changed."""
    link_dir = os.path.dirname(link_path) or "."
    rel_target = os.path.relpath(target, link_dir)

    if os.path.islink(link_path) and os.readlink(link_path) == rel_target:
        logging.info(f"{link_path} already points to {rel_target}.")
        return False

    if os.path.islink(link_path) and os.lstat(link_path).st_mtime > os.path.getmtime(target):
        logging.warning(f"{link_path} appears to have been manually changed — leaving it alone.")
        return False

    parent = os.path.dirname(link_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    if os.path.lexists(link_path):
        os.remove(link_path)
    os.symlink(rel_target, link_path)
    logging.info(f"Updated {link_path} -> {rel_target}.")
    return True


# ── Main ──────────────────────────────────────────────────────────────────────


def resolve_asset(asset_name, platform=None):
    """Look up asset in registry and resolve its latest version.
    Returns (filename, download_url, sha256).
    """
    if asset_name not in ASSETS:
        raise ValueError(f"Unknown asset: {asset_name}")

    conf = {"project": asset_name}
    conf.update(ASSETS[asset_name])
    if platform:
        conf["platform"] = platform

    source = conf["source"]
    fetcher = FETCHERS.get(source)
    if not fetcher:
        raise ValueError(f"Unknown source type: {source}")

    return fetcher(conf)


def main(config_path="update.conf"):
    logging.info("--- Starting update run ---")

    # 1. Load config
    config = load_config(config_path)
    global_hook = config.get("restart_hook")
    servers = config.get("servers", {})

    # 2. Build placements: (asset_name, platform, server_name, dest_path)
    placements = []
    for server_name, server_conf in servers.items():
        root = server_conf.get("root", f"./{server_name}")
        server_asset = server_conf["server"]
        placements.append((server_asset, None, server_name, f"{root}/{server_asset}.jar"))
        platform = SERVER_PLATFORMS.get(server_asset)
        for plugin_name in server_conf.get("plugins", []):
            placements.append((plugin_name, platform, server_name, f"{root}/plugins/{plugin_name}.jar"))

    # 3. Check for dest collisions
    seen = {}
    for asset_name, _platform, _server_name, dest in placements:
        if dest in seen:
            raise RuntimeError(
                f"Error: {asset_name} and {seen[dest]} both symlink to {dest}"
            )
        seen[dest] = asset_name

    # 4. Resolve unique assets (dedup by name + platform)
    unique_assets = {}
    for asset_name, platform, _, _ in placements:
        key = (asset_name, platform)
        if key not in unique_assets:
            logging.info(f"Resolving {asset_name}{' (' + platform + ')' if platform else ''}...")
            unique_assets[key] = resolve_asset(asset_name, platform)

    # 5. Download
    for (asset_name, _platform), (filename, url, sha256) in unique_assets.items():
        download_file(filename, url, sha256)

    # 6. Update symlinks
    changed_servers = set()
    for asset_name, platform, server_name, dest in placements:
        filename = unique_assets[(asset_name, platform)][0]
        download_path = os.path.join(DOWNLOADS_DIR, filename)
        if update_symlink(download_path, dest):
            changed_servers.add(server_name)

    # 7. Run restart hooks
    for server_name in changed_servers:
        hook = servers[server_name].get("restart_hook")
        if hook:
            logging.info(f"Running restart hook for {server_name}: {hook}")
            subprocess.run(hook, shell=True, check=True)

    if changed_servers and global_hook:
        logging.info(f"Running global restart hook: {global_hook}")
        subprocess.run(global_hook, shell=True, check=True)

    logging.info("--- Done ---")


if __name__ == "__main__":
    config_path = sys.argv[1] if len(sys.argv) > 1 else "update.conf"
    setup_logging()
    try:
        main(config_path)
    except Exception as e:
        logging.error(str(e))
        logging.info("--- Exited ---")
        sys.exit(1)
