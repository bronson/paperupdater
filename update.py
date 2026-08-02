#!/usr/bin/env python3
"""Paper Updater — Multi-server and plugin updater for Minecraft."""

import email.header
import glob
import hashlib
import logging
import os
import shutil
import subprocess
import sys
import time
from collections import namedtuple

import requests

# ── Constants ─────────────────────────────────────────────────────────────────

USER_AGENT = "PaperUpdater/2.0 (https://github.com/bronson/paperupdater)"
HEADERS = {"User-Agent": USER_AGENT}
LOG_FILENAME = "paper_updater.log"
LOG_FORMAT = "%(asctime)s %(message)s"
DOWNLOADS_DIR = "downloads"

PAPER_API_BASE = "https://fill.papermc.io/v3"
HANGAR_API_BASE = "https://hangar.papermc.io/api/v1"
# Hangar caps the page size at 25 results; fetch_hangar pages through at this size.
HANGAR_PAGE_SIZE = 25
GITHUB_API_BASE = "https://api.github.com"

# ── Asset Registry ────────────────────────────────────────────────────────────
# Each entry maps an asset name to its source details.
# Inferral: "project" defaults to the asset name.

SERVER_PLATFORMS = {
    "paper": "PAPER",
    "velocity": "VELOCITY",
    "waterfall": "WATERFALL",
}

# Server platforms whose resolved version is a Minecraft version, used to match
# plugin builds to the running server. Proxies like Velocity (whose version is
# the proxy version, not Minecraft's) are excluded.
MC_VERSIONED_SERVERS = {"paper", "waterfall"}

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

AssetInfo = namedtuple("AssetInfo", "filename url sha256 download_glob mc_version", defaults=(None,))
Deployment = namedtuple("Deployment", "asset_name platform server_name path")


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
# Each fetcher takes a config dict and returns an AssetInfo.
# The filename must uniquely identify that asset, so version at a minimum, and also any variants.
# For example: `floodgate-velocity-2.2.5.jar` is good, `floodgate-latest.jar` is bad.
# sha256 is None when the source doesn't provide checksums (e.g. GitHub).
# If the source *should* provide a checksum but didn't, the fetcher raises an error
# (downgrade attack protection).
# To add a fetcher, define a new function and add it to FETCHERS.


def filename_from_url(url, fallback_version):
    """Determine a filename from a URL's Content-Disposition header and redirect path."""
    head = requests.head(url, headers=HEADERS, allow_redirects=True)
    cd = head.headers.get("Content-Disposition", "")
    filename = fallback_version + ".jar"
    if 'filename="' in cd:
        raw = cd.split('filename="')[1].split('"')[0]
        # Decode RFC 2047 encoding (e.g. =?UTF-8?Q?floodgate-spigot.jar?=)
        decoded_parts = email.header.decode_header(raw)
        filename = ''.join(
            part.decode(enc or 'utf-8') if isinstance(part, bytes) else part
            for part, enc in decoded_parts
        )
    # Extract version from the final redirect URL
    # (e.g. /versions/2.9.6/builds/1132/downloads/spigot)
    ver = fallback_version
    parts = head.url.split("/")
    for i, part in enumerate(parts):
        if part == "versions" and i + 1 < len(parts):
            ver = parts[i + 1]
            break
    base, ext = os.path.splitext(filename)
    return f"{base}-{ver}{ext}"


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
            full_version = f"{version}-{build['id']}"
            mc_version = version if project in MC_VERSIONED_SERVERS else None
            return AssetInfo(dl["name"], dl["url"], sha256, version_glob(dl["name"], full_version), mc_version)

    raise RuntimeError(f"No stable builds found for {project}.")


def _hangar_supports(version_entry, platform, mc_version):
    """True if a Hangar version declares support for mc_version on platform."""
    deps = version_entry.get("platformDependencies", {})
    return mc_version in deps.get(platform, [])


def _iter_hangar_versions(project):
    """Yield all Hangar versions for a project, newest-first, paging as needed."""
    offset = 0
    while True:
        resp = fetch(f"{HANGAR_API_BASE}/projects/{project}/versions"
                     f"?limit={HANGAR_PAGE_SIZE}&offset={offset}")
        result = resp.json().get("result", [])
        if not result:
            return
        yield from result
        if len(result) < HANGAR_PAGE_SIZE:
            return
        offset += HANGAR_PAGE_SIZE


def _hangar_download(project, version, platform):
    """Return an AssetInfo for a Hangar version's build, or None if it has no
    usable build for the requested platform.

    Prefers the Hangar-hosted build (which carries a checksum); falls back to an
    external URL (no checksum). When `platform` is given, only that platform's
    build is considered — we never silently substitute another platform's jar.
    """
    downloads = version.get("downloads", {})
    if platform:
        platform_names = [platform] if platform in downloads else []
    else:
        platform_names = list(downloads)

    for platform_name in platform_names:
        dl = downloads[platform_name]
        if dl.get("downloadUrl") and dl.get("fileInfo"):
            fi = dl["fileInfo"]
            sha256 = fi.get("sha256Hash")
            if not sha256:
                raise RuntimeError(
                    f"Downgrade attack suspected: Hangar did not provide a checksum "
                    f"for {project} {version['name']} ({platform_name})"
                )
            return AssetInfo(fi["name"], dl["downloadUrl"], sha256,
                             version_glob(fi["name"], version["name"]), None)

    for platform_name in platform_names:
        dl = downloads[platform_name]
        if dl.get("externalUrl"):
            url = dl["externalUrl"]
            filename = filename_from_url(url, version["name"])
            logging.warning(
                f"External download for {project} — no checksum available. "
                f"Proceeding without verification for {filename}"
            )
            return AssetInfo(filename, url, None, version_glob(filename, version["name"]), None)

    return None


def fetch_hangar(conf):
    """Resolve the newest Hangar release compatible with the target.

    Versions are scanned newest-first (paging if necessary); the first that both
    declares support for the requested Minecraft version (when `mc_version` is
    set) and offers a build for the requested platform is returned. If no
    compatible version exists anywhere in the project's history, this raises —
    the same fail-loud policy used for missing checksums.

    This matters because some plugins publish parallel release lines per
    Minecraft version (e.g. squaremap 1.3.15 for 26.2 alongside 1.3.13.1 for
    26.1.2); taking the most-recently-published version would grab the wrong
    branch.
    """
    project = conf["project"]
    platform = conf.get("platform")
    mc_version = conf.get("mc_version")

    for version in _iter_hangar_versions(project):
        if mc_version and not _hangar_supports(version, platform, mc_version):
            continue
        asset = _hangar_download(project, version, platform)
        if asset is not None:
            return asset

    mc_part = f" MC {mc_version}" if mc_version else ""
    raise RuntimeError(f"No {project} release on Hangar has a build for {platform or 'any'}{mc_part}.")


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
    tag = release.get("tag_name", "").lstrip("v")
    return AssetInfo(asset["name"], asset["browser_download_url"], None, version_glob(asset["name"], tag), None)


def version_glob(filename, version):
    """Replace the known version in filename with '*' to create a glob pattern.
    Returns None if version is empty or not found in filename.
    """
    if not version:
        return None
    idx = filename.find(version)
    if idx < 0:
        return None
    return filename[:idx] + "*" + filename[idx + len(version):]


FETCHERS = {
    "papermc": fetch_papermc,
    "hangar":  fetch_hangar,
    "github":  fetch_github,
}


# ── Config ────────────────────────────────────────────────────────────────────


def load_config(path="update.conf.py"):
    """Load a Python config file. Returns its globals as a dict."""
    config = {}
    try:
        with open(path) as f:
            exec(f.read(), config)  # noqa: S102
    except OSError as e:
        raise OSError(f"Can't read config file: {e}") from e
    return config


# ── Prune ─────────────────────────────────────────────────────────────────────


def reconcile_plugins(root, configured_plugins):
    """Remove plugin jars from {root}/plugins that are no longer configured.

    Only jars whose stem is a managed asset (a key in ASSETS) and that are not
    listed in configured_plugins are removed. Manually-installed jars that this
    tool doesn't manage are always left untouched. Returns True if any file
    was removed.
    """
    plugins_dir = os.path.join(root, "plugins")
    if not os.path.isdir(plugins_dir):
        return False

    configured = set(configured_plugins)
    removed = False
    for jar in glob.glob(os.path.join(plugins_dir, "*.jar")):
        stem = os.path.splitext(os.path.basename(jar))[0]
        if stem in ASSETS and stem not in configured:
            logging.info(f"Removing disabled plugin: {jar}")
            os.remove(jar)
            removed = True
    return removed


def prune_old_downloads(download_glob, keep):
    """Remove old versions of the same artifact, keeping the newest `keep` files."""
    if not download_glob or keep is None or keep <= 0:
        return

    pattern = os.path.join(DOWNLOADS_DIR, download_glob)
    matches = sorted(glob.glob(pattern), key=os.path.getmtime)

    while len(matches) > keep:
        old = matches.pop(0)
        logging.info(f"Pruning old download: {old}")
        os.remove(old)


# ── Download ──────────────────────────────────────────────────────────────────


def download_file(asset):
    """Download an asset into DOWNLOADS_DIR, verifying checksum if available.

    asset.sha256 is None when the source doesn't provide checksums (download
    proceeds with a warning). If a checksum is provided and doesn't match,
    the file is deleted and ValueError is raised.
    """
    filepath = os.path.join(DOWNLOADS_DIR, asset.filename)

    # Already downloaded and verified?
    if os.path.isfile(filepath):
        if asset.sha256:
            try:
                verify_checksum(filepath, asset.sha256)
                logging.info(f"Already have {asset.filename} — no download needed.")
                return
            except ValueError:
                logging.warning(f"{asset.filename} exists but checksum mismatch — re-downloading.")
                os.remove(filepath)
        else:
            logging.info(f"Already have {asset.filename} — no checksum to verify, assuming correct.")
            return

    # Download
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    response = fetch(asset.url)
    with open(filepath, "wb") as f:
        f.write(response.content)
    logging.info(f"Saved to {filepath}.")

    # Verify
    if asset.sha256:
        try:
            verify_checksum(filepath, asset.sha256)
        except ValueError as e:
            os.remove(filepath)
            raise
    else:
        logging.warning(f"No checksum available for {asset.filename} — download not verified.")


# ── Symlinks ──────────────────────────────────────────────────────────────────


def deploy_file(src, dst):
    """Copy src to dst. Returns True always."""
    parent = os.path.dirname(dst)
    if parent:
        os.makedirs(parent, exist_ok=True)
    if os.path.lexists(dst):
        os.remove(dst)
    shutil.copy2(src, dst)
    logging.info(f"Deployed {src} -> {dst}.")
    return True


# ── Main ──────────────────────────────────────────────────────────────────────


def resolve_asset(asset_name, platform=None, mc_version=None):
    """Look up asset in registry and resolve its latest version.
    Returns an AssetInfo.
    """
    if asset_name not in ASSETS:
        raise ValueError(f"Unknown asset: {asset_name}")

    conf = {"project": asset_name}
    conf.update(ASSETS[asset_name])
    if platform:
        conf["platform"] = platform
    if mc_version:
        conf["mc_version"] = mc_version

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
    deployments = []
    for server_name, server_conf in servers.items():
        root = server_conf.get("root", f"./{server_name}")
        server_asset = server_conf["server"]
        deployments.append(Deployment(server_asset, None, server_name, f"{root}/{server_asset}.jar"))
        platform = SERVER_PLATFORMS.get(server_asset)
        for plugin_name in server_conf.get("plugins", []):
            deployments.append(Deployment(plugin_name, platform, server_name, f"{root}/plugins/{plugin_name}.jar"))

    # 3. Check for path collisions
    seen = {}
    for d in deployments:
        if d.path in seen:
            raise RuntimeError(
                f"Error: {d.asset_name} and {seen[d.path]} both symlink to {d.path}"
            )
        seen[d.path] = d.asset_name

    # 4. Resolve unique assets (dedup by name + platform)
    unique_assets = {}
    server_mc_versions = {}  # server_name -> running Minecraft version (if MC-versioned)
    for d in deployments:
        key = (d.asset_name, d.platform)
        is_server = d.asset_name in SERVER_PLATFORMS
        if key not in unique_assets:
            # Match plugins to their server's Minecraft version, if known.
            mc_version = server_mc_versions.get(d.server_name)
            logging.info(f"Resolving {d.asset_name}{' (' + d.platform + ')' if d.platform else ''}...")
            unique_assets[key] = resolve_asset(d.asset_name, d.platform, mc_version=mc_version)
        if is_server:
            server_mc_versions[d.server_name] = unique_assets[key].mc_version

    # 5. Download
    for asset in unique_assets.values():
        download_file(asset)

    # 6. Update symlinks and prune old downloads
    changed_servers = set()
    versions_to_keep = config.get("versions_to_keep", 4)
    for d in deployments:
        asset = unique_assets[(d.asset_name, d.platform)]
        asset_path = os.path.join(DOWNLOADS_DIR, asset.filename)
        if deploy_file(asset_path, d.path):
            changed_servers.add(d.server_name)
            prune_old_downloads(asset.download_glob, versions_to_keep)

    # 7. Remove plugins that have been disabled (no longer in the config)
    for server_name, server_conf in servers.items():
        root = server_conf.get("root", f"./{server_name}")
        if reconcile_plugins(root, server_conf.get("plugins", [])):
            changed_servers.add(server_name)

    # 8. Run restart hooks
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
    config_path = sys.argv[1] if len(sys.argv) > 1 else "update.conf.py"
    setup_logging()
    try:
        main(config_path)
    except Exception as e:
        logging.error(str(e))
        logging.info("--- Exited ---")
        sys.exit(1)
