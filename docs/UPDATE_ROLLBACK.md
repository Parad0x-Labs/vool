# Returning to the previous VOOL version

After a successful signed update, the header update menu and **Settings > About**
offer **Roll back to <version>** when a compatible previous copy is retained.
Press it to restore that copy's backend and frontend code. Your current chats,
memory, facts, keys and settings stay in the existing runtime home, including
records created after the update. Destructive work must finish first.

The terminal command also works when VOOL cannot start. Use the same runtime
home and installation that you normally use. It asks a running app to honor its
work gate; when the API is unavailable it starts the detached recovery helper.

For a Mac bundle, set `APP_BUNDLE` to your existing bundle and run:

```sh
cd "$APP_BUNDLE/Contents/Resources/app"
../python/bin/python3 -m installer.update_cli rollback --i-pressed-rollback --wait
```

For a Linux installer-source installation:

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
