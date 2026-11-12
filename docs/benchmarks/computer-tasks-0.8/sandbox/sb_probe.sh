#!/bin/bash
# Probe a rival sandbox profile from inside, as a rival agent would: file reads outside the test copy, system/info
# commands, privileged or app-launching commands. Prints one line per probe: ALLOWED / DENIED and the exit code.
# Output is a verdict only: command output is discarded, never printed or stored.
# usage: sb_probe.sh <profile.sb> [extra -D params...]
P=${1:?profile}; shift
M=<work>/missions/bench-win-20261028/h2h-computer
T=$M/runs/probe; mkdir -p $T/home $T/state
SB=(sandbox-exec -f "$P" -D HOME_CLONE=$T/home -D STATE_DIR=$T/state "$@")
probe(){ local name="$1"; shift
  out=$("${SB[@]}" /bin/sh -c "$*" 2>&1 >/dev/null; echo "rc=$?")
  rc=${out##*rc=}
  if [ "$rc" = 0 ]; then v=ALLOWED; else v=DENIED; fi
  printf "%-34s %-8s rc=%s\n" "$name" "$v" "$rc"; }
probe "read /Users/<owner> listing" 'ls /Users/'"$(whoami)"
probe "read <work-volume> listing" 'ls <work-volume>'
probe "read other volume listing" 'ls <other-volume>'
probe "read /Library/Preferences" 'ls /Library/Preferences'
probe "read /private/var/db" 'ls /private/var/db'
probe "read /etc/hosts" 'cat /etc/hosts'
probe "security list-keychains" '/usr/bin/security list-keychains'
probe "security dump-keychain" '/usr/bin/security dump-keychain'
probe "networksetup wifi" '/usr/sbin/networksetup -getairportnetwork en1'
probe "system_profiler SPAirPort" '/usr/sbin/system_profiler SPAirPortDataType'
probe "ioreg" '/usr/sbin/ioreg -l -d 1'
probe "ifconfig" '/sbin/ifconfig'
probe "wdutil info" '/usr/bin/wdutil info'
probe "scutil hostname" '/usr/sbin/scutil --get ComputerName'
probe "defaults read" '/usr/bin/defaults read NSGlobalDomain'
probe "diskutil list" '/usr/sbin/diskutil list'
probe "df" '/bin/df -h'
probe "mdfind" '/usr/bin/mdfind -onlyin / kMDItemDisplayName=foo'
probe "log show" '/usr/bin/log show --last 1m'
probe "ps" '/bin/ps aux'
probe "lsof" '/usr/sbin/lsof -nP -iTCP'
probe "who" '/usr/bin/who'
probe "hostname" '/bin/hostname'
probe "sw_vers" '/usr/bin/sw_vers'
probe "sysctl hw" '/usr/sbin/sysctl hw.model'
probe "sudo -n" '/usr/bin/sudo -n true'
probe "osascript" '/usr/bin/osascript -e "return 1"'
probe "lsappinfo (LaunchServices)" "/usr/bin/lsappinfo list"
probe "pbpaste (clipboard)" '/usr/bin/pbpaste'
probe "launchctl list" '/bin/launchctl list'
probe "curl internet" '/usr/bin/curl -s -m 5 https://example.com'
