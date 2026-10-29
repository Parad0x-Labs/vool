# Returning to the previous VOOL version

The shipped release feed is checked only while **Go online on its own** is on
(Settings > Privacy & Permissions). With it on, VOOL checks the feed when it
starts and then once a day, and the header shows **Update ready** when a newer
version is out. With it off, nothing is fetched and the header shows
**Update checks off**. **Check for updates** checks right away.
`VOOL_UPDATE_DISABLED=1` disables the feed; a `VOOL_UPDATE_MANIFEST_URL`
override points it at another feed. The first-run egress census reports the
update door as closed while the switch is off.

After a successful signed update, the header update menu and **Settings > About**
offer **Roll back to <version>** when a compatible previous copy is retained.
Press it to restore that copy's backend and frontend code. Your current chats,
memory, facts, keys and settings stay in the existing runtime home, including
records created after the update. Destructive work must finish first.

The Linux download (the tarball install in `~/.local/share/vool`) does not offer
**Roll back** yet. Its update is installed next to the running version, and the
previous version folder is kept. If the new version does not start properly,
VOOL switches `current` back to the previous version by itself.

The terminal command also works when VOOL cannot start. Use the same runtime
home and installation that you normally use. It asks a running app to honor its
work gate; when the API is unavailable it starts the detached recovery helper.

For a Mac bundle, set `APP_BUNDLE` to your existing bundle and run:

```sh
cd "$APP_BUNDLE/Contents/Resources/app"
../python/bin/python3 -m installer.update_cli rollback --i-pressed-rollback --wait
```

For a Linux installer-source installation (not the tarball install):

```sh
cd "$VOOL_INSTALL_DIR"
./.venv/bin/python -m installer.update_cli rollback --i-pressed-rollback --wait
```

If you normally set `VOOL_HOME`, retain that value. A custom API port can be
specified with `--health-url http://127.0.0.1:PORT/healthz`. The command waits
up to 180 seconds and exits successfully only after a healthy previous runtime
has been recorded. Omitting `--wait` reports whether the operation started.

The retained code's integrity and OS/CPU are checked before stopping VOOL.
An absent or damaged backup, applied data migration, or incompatible/unknown
recorded data schema refuses rollback without reverting your current data.
Successful manual rollback never restores the original update's older data
snapshot. If the previous runtime fails, recovery instead restores the current
code and the fresh data/config/history snapshot taken for this rollback.

If interrupted, the next launcher startup recovers the durable rollback state.
Incomplete recovery is reported explicitly and retains its backups under the
existing data directory's `update_v2/manual_rollbacks/`; do not delete them.
Automatic failed-update recovery remains separate and uses the pre-update
snapshot. The publisher replay counter is never rewound by either operation.
