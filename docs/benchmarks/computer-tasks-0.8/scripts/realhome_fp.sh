#!/bin/bash
# Read-only fingerprint of the real home's protected folders: path, size, mtime of every file (maxdepth 6, no content read),
# hashed. Photos Library.photoslibrary is listed as one entry (macOS updates it on its own).
cd "$HOME" || exit 1
for d in Desktop Documents Downloads Pictures Movies Music; do
  [ -d "$d" ] && find "$d" -maxdepth 6 \( -name "*.photoslibrary" -prune -print \) -o -type f -print 2>/dev/null
done | sort | while IFS= read -r f; do case "$f" in *.photoslibrary) echo "$f (photos library, mtime ignored)";; *) stat -f "%N %z %m" "$f" 2>/dev/null;; esac; done > "$1.list"
md5 -q "$1.list"; wc -l < "$1.list"
