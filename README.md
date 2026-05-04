# Paper Updater

Based on:
* [PaperUpdater](https://github.com/jc0b/PaperUpdater)
* [MinecraftServerUpdater](https://github.com/eclair4151/MinecraftUpdater)

This script checks to see if there are newer versions of your
Minecraft server or plugins available. If so, it downloads them, verifies the
signatures, and copies them into your game directory.

## Features

- supports multiple servers
- verifies checksums
- prunes old versions of files

## Installation

Single server? Clone paperudater into your Minecraft directory.

TODO: need to verify this use case.

Multiple servers? Clone paperupdater into a directory somewhere above your server directories.

### Config File

## Usage

```sh
./updater.py [config_file]
```

If you don't specify a config file on the command line, the script will read from `update.conf.py`.

## Download Process

Logs are written to `paper_updater.log`, in the same directory as the update script is run.


## Config Options

### versions_to_keep: Pruning Old Downloads

To prevent obsolete files from building up in your Downloads directory,
if a new version of an artifact was successfully installed, old versions
can optionally be pruned. The versions_to_keep option specifies how many
old versions of files you'd like to keep around (defaults to 4).

```
    versions_to_keep = 8
```

Set versions_to_keep to None to skip pruning.

### restart_hook: restarts the server when new files are installed

A restart hook is a shell command that can restart a particular server.

Each server can have a restart_hook, and there's a global restart hook.
If any server has received updated files, that server's restart hook
is called. If any server's restart hook was called, the global restart
hook will be called last.

## License

MIT