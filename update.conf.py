# paperupdater config
#
# This configuration file tells paperupdater what assets to download and install.

# If anything was installed during this update run, this hook is called
# (as well as the individual server restart hooks).
restart_hook = "systemctl restart minecraft"

# How many old versions of each asset to keep in the downloads directory.
# Default is 4. Set to 0 or None to disable pruning.
versions_to_keep = 4

servers = {
    "velocity": {
        "server": "velocity",
        "root": "/opt/minecraft/velocity",  # defaults to "./{server_name}" so can usually be omitted
        "plugins": ["geyser", "floodgate"],
        # if a new velocity server or any of its plugins were installed, this restart hook will be called
        "restart_hook": "systemctl restart velocity",
    },
    "searanch": {
        "server": "paper",
        "plugins": ["squaremap", "discordsrv", "simple-voice-chat"],
        "restart_hook": "systemctl restart searanch",
    },
    "benworld": {
        "server": "paper",
        "plugins": ["squaremap"],
        "restart_hook": "systemctl restart benworld",
    },
}