#!/bin/sh
# Install the versioned pre-push guard into this clone's .git/hooks.
set -e
repo="$(git rev-parse --show-toplevel)"
hooks="$(git rev-parse --git-path hooks)"
case "$hooks" in /*) : ;; *) hooks="$repo/$hooks" ;; esac
mkdir -p "$hooks"
cp "$repo/scripts/hooks/pre-push" "$hooks/pre-push"
chmod +x "$hooks/pre-push"
echo "Installed pre-push guard -> $hooks/pre-push"
echo "Reminder: this hook is an accident guard only; GitHub branch protection is the backstop."
