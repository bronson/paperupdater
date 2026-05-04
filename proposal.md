# Paper Updater — Multi-Server + Plugin Support

## Goals

Extend `update.py` to support:
- Updating **plugins** as well as servers
- Updating **multiple servers** in one session
- Sharing downloaded binaries across servers (dedup)
- All with minimal, readable code and minimal dependencies

Requirements:
- **Idempotent** — safe to run multiple times
- **Python 3.11+** (we can use `tomllib`)
- **Atomic updates** via symlinks from `downloads/` to destination
- **Checksums verified** whenever the source provides them
  - If a source *claims* to provide checksums but none are available, that's an **error** (prevents downgrade attacks)
  - If a source simply doesn't provide checksums, best effort — download proceeds with a warning

## Asset Registry (in `update.py`)

Servers and plugins are both **assets** — they share the same registry schema.
The registry is a dict mapping asset names to their source details.
Updated with the script; not something users edit per-instance.

```python
ASSETS = {
    # Servers
    "paper":              {"source": "papermc"},
    "velocity":           {"source": "papermc"},
    # Plugins
    "geyser":             {"source": "hangar", "project": "GeyserMC/Geyser"},
    "floodgate":          {"source": "hangar", "project": "GeyserMC/Floodgate"},
    "squaremap":           {"source": "hangar"},
    "discordsrv":         {"source": "github", "project": "DiscordSRV/DiscordSRV"},
    "simple-voice-chat":   {"source": "hangar"},
}
```

**Inferral rules** (applied when registry entry is missing a field):
- `project` defaults to the asset name (e.g., `"squaremap"` → `project = "squaremap"`)
- `source = "papermc"` is implied for known server platforms (`paper`, `velocity`, `waterfall`, etc.)
- Server jar dest = `{root}/{asset_name}.jar`
- Plugin jar dest = `{root}/plugins/{asset_name}.jar`

## User Config (`config.py`)

Just says which assets each server instance uses. No source details, no repetition.

```python
restart_hook = "systemctl restart minecraft"   # global hook: runs once if anything changed

servers = {
    "velocity": {
        "assets": ["velocity", "geyser", "floodgate"],
        "restart_hook": "systemctl restart velocity",
    },
    "searanch": {
        "assets": ["paper", "squaremap", "discordsrv", "simple-voice-chat"],
        "restart_hook": "systemctl restart searanch",
    },
    "benworld": {
        "assets": ["paper", "squaremap"],
        "restart_hook": "systemctl restart benworld",
    },
}
```

## How It Works

### 1. Parse config, build asset list

Read `config.py`. Collect all unique assets across all servers (dedup by source + project).
Each asset knows its destinations (potentially multiple — e.g., `hangar:squaremap` is used by both searanch and benworld).

### 2. For each unique asset, resolve latest version

Each `source` maps to a fetcher function. Adding a new source = writing one function.

| source     | API                       | Example project         |
|------------|---------------------------|-------------------------|
| `papermc`  | fill.papermc.io           | `paper`, `velocity`     |
| `hangar`   | Hangar (PaperMC plugins)  | `squaremap`, `GeyserMC/Geyser` |
| `github`   | GitHub Releases           | `DiscordSRV/DiscordSRV` |

Each fetcher returns:
- `filename` — canonical name for the downloaded file (e.g., `paper-1.21.4-123.jar`)
- `download_url`
- `sha256` — or `None` if the source doesn't provide checksums

### 3. Check for collisions

After resolving all assets, before downloading anything, check that no two assets share the same symlink destination:

```python
seen = {}
for asset_name, dest in all_destinations:
    if dest in seen:
        raise Error(f"{asset_name} and {seen[dest]} both symlink to {dest}")
    seen[dest] = asset_name
```

Two different assets could have different versioned filenames in `downloads/` (e.g., `geyser-2.0.jar` vs `geyser-3.0.jar`) but still collide on the symlink dest (`./searanch/plugins/geyser.jar`). Checking dest paths catches this. Hard fail before any network activity.

### 4. Download into `downloads/` (if not already present)

- Check if `downloads/{filename}` exists and (if checksum available) verify it matches
- If not present or checksum mismatch, download it
- Verify checksum after download if one was provided
- If the source *normally* provides checksums but didn't return one, **error out** (downgrade attack protection)
- If the source is known to not provide checksums, log a warning and proceed

### 5. Update symlinks

For each asset destination in the config:
- Point the symlink at `downloads/{filename}`
- If the symlink already points to the correct file, skip (no change)
- If the symlink was manually changed (points to a different file), log a warning and skip
  - **Phase 2**: version pinning in config will replace this entirely
- Atomic swap: remove old symlink, create new one

### 6. Run restart hooks

- Track which servers had assets updated
- Run each updated server's `restart_hook`
- If any server was updated, run the global `restart_hook`
- Only run hooks if at least one asset actually changed

## Decisions

- **Explicit `dest` omitted** — inferred from server root + plugin name. Can be overridden per-asset if needed.
- **Restart hooks are shell commands** — simple and flexible.
- **Phase 1 fetchers**: `papermc`, `github`, `hangar`. Others (Modrinth, SpigotMC, CurseForge, direct URLs) can be added later. The fetcher interface is simple and each one is self-contained.
- **Python config file** — natural nesting, zero-cost parsing (`exec` the file, iterate the dict). The security concern of `exec()` is irrelevant for a personal server tool.

## Phases

- **Phase 2 — Version pinning**: Add optional `version = "1.21"` to any asset. If present, resolve the latest build within that version series. This replaces symlink monitoring entirely — if you want to stay on an old version, just pin it.
- **Phase 3 — Discord notifications**: Post to a webhook when updates start/finish, what changed, and especially on errors.
- **Phase 4 — More fetchers**: Modrinth, SpigotMC, CurseForge, direct URLs, etc.
