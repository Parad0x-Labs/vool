"""Build the native app's small static catalogs from the desktop source of truth."""

import argparse
import ast
import json
import re
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--extra-file")
parser.add_argument("--check", action="store_true")
args = parser.parse_args()
mobile = Path(__file__).resolve().parents[1]
root = mobile.parent / "core/i18n/catalogs"
source = {p.stem: json.loads(p.read_text())["messages"] for p in root.glob("*.json")}
if args.extra_file:
    extras = json.loads(Path(args.extra_file).read_text())
    for lang, values in extras.items():
        source[lang].update(values)
common = [
    "home.wallet",
    "sidebar.chats",
    "xact.decline",
    "addons.cancel",
    "permissions.deny",
    "permissions.allow_once",
    "setup.copy.back",
    "notif.save",
    "attach.retry",
    "composer.send",
    "run.stop",
    "chats.filter_aria",
    "notif.pop_title",
    "activity.ev.task_completed",
    "activity.ledger.task_failed",
    "xact.state_declined",
    "xact.state_expired",
]
keys = {k for k in source["en"] if k.startswith("phone.")} | set(common)
messages = {lang: {key: values[key] for key in sorted(keys)} for lang, values in sorted(source.items())}
index = {}
for key in sorted(keys):
    index.setdefault(source["en"][key], key)
required = set()
for path in [mobile / "App.tsx", *sorted((mobile / "src").rglob("*.tsx"))]:
    for match in re.finditer(r'\btr\(("(?:\\.|[^"\\])*"|\x27(?:\\.|[^\x27\\])*\x27)', path.read_text()):
        required.add(ast.literal_eval(match[1]))
# Reuse exact existing desktop copy before adding another translation key.
labels = (mobile / "src/screens/NotificationsScreen.tsx").read_text()
for block in re.findall(r"const LABELS[^=]*=\s*\{(.*?)\};", labels, re.S):
    for value in re.findall(r':\s*("(?:\\.|[^"\\])*")', block):
        required.add(ast.literal_eval(value))
required.update(["Everything", "Important only", "Off"])
for presentation in sorted(required):
    if presentation not in index:
        matches = [key for key, value in source["en"].items() if value == presentation]
        if matches:
            key = sorted(matches)[0]
            keys.add(key)
            index[presentation] = key
messages = {lang: {key: values[key] for key in sorted(keys)} for lang, values in sorted(source.items())}
missing = sorted(required - index.keys())
if missing:
    raise SystemExit("Missing native translations: " + json.dumps(missing, ensure_ascii=False))
text = (
    "// Static presentation copy; authority values are never translated.\nexport const messages = "
    + json.dumps(messages, ensure_ascii=False, sort_keys=True, indent=2)
    + " as const;\nexport const sourceIndex = "
    + json.dumps(index, ensure_ascii=False, sort_keys=True, indent=2)
    + " as const;\n"
)
output = mobile / "src/i18n/messages.ts"
if args.check:
    assert output.read_text() == text, "Native catalogs are stale; run scripts/sync_i18n.py"
else:
    output.write_text(text)
print(len(keys), "keys,", len(messages), "languages,", len(required), "literal UI messages checked")
