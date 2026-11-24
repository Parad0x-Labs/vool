"""One listing object for a skill and for a plugin, and what an author may put in it.

The description is a small part of Markdown read into a tree of text; the author tool and the upload refuse
what a page would not show, each time with the field and the fix; and the service answers one object that
matches the published schema, built from the package, the store and the verified badge document.
"""
import json
import re
import shutil
from pathlib import Path

import pytest

from core.registry import listing
from core.registry.artifact import inspect
from registry_service.api import PREFIX
from registry_service.authority import mit_license
from tests.registry.badge_helpers import BUILD, payload, raw_document, relay
from tests.registry.badge_helpers import entry as badge_entry
from tests.registry.badge_helpers import world as badge_world
from tests.registry.helpers import NOW
from tests.registry.test_plugins_in_the_skill_codex import folder, send, tool
from tests.registry.test_plugins_in_the_skill_codex import listing as plugin_listing_file
from tests.registry.test_skill_codex_authority import creator, rows
from tests.registry.test_skill_codex_authority import world as world
from tools import community_skills

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "docs" / "reference" / "listing-standard-v1.schema.json").read_text(encoding="utf-8"))
DESCRIPTION = """# Meeting notes

Turns the notes of a meeting into **decisions**, *owners* and dates. Paste the notes and say `list the decisions`.

## What you get

- one line for each decision
- who owns it
  and when it is due

1. Paste the notes.
2. Read the list.

```
Decision: ship on Friday (owner: Ana)
```
"""
SKILL = ("---\nname: meeting-notes\ndescription: Turn meeting notes into decisions\n---\n\n"
         "Read the meeting notes the user gives you. List each decision, who owns it and when it is due. "
         "Ask before sending anything anywhere.\n")


# ---- a small reader for the schema: every keyword the file uses, and a failure for one it does not know ----

_KNOWN = {"$schema", "$id", "title", "description", "type", "additionalProperties", "required", "properties", "const",
          "enum", "pattern", "minLength", "maxLength", "minimum", "maximum", "items", "uniqueItems", "minItems",
          "maxItems", "allOf", "if", "then", "$ref", "$defs", "format", "x-by", "x-from"}
_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def _is(value, name):
    if name == "integer":
        return type(value) is int
    if name == "number":
        return type(value) in (int, float)
    return isinstance(value, _TYPES[name]) and not (name != "boolean" and isinstance(value, bool))


def problems(value, schema, path="$"):
    assert set(schema) <= _KNOWN, (path, set(schema) - _KNOWN)
    if "$ref" in schema:
        return problems(value, SCHEMA["$defs"][schema["$ref"].rsplit("/", 1)[1]], path)
    found = []
    if "const" in schema and value != schema["const"]:
        found.append(path + ": not " + repr(schema["const"]))
    if "enum" in schema and value not in schema["enum"]:
        found.append(path + ": not one of the listed values")
    if "type" in schema and not any(_is(value, name) for name in ([schema["type"]] if isinstance(schema["type"], str) else schema["type"])):
        return [*found, path + ": wrong type"]
    if isinstance(value, str):
        if "pattern" in schema and not re.search(schema["pattern"], value):
            found.append(path + ": pattern")
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 10**9):
            found.append(path + ": length")
        if schema.get("format") == "uri" and not re.fullmatch(r"[a-z][a-z0-9+.-]*:\S+", value):
            found.append(path + ": not an address")
    if type(value) in (int, float) and not schema.get("minimum", value) <= value <= schema.get("maximum", value):
        found.append(path + ": out of range")
    if isinstance(value, list):
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 10**9):
            found.append(path + ": item count")
        if schema.get("uniqueItems") and len({json.dumps(item, sort_keys=True) for item in value}) != len(value):
            found.append(path + ": repeated item")
        for index, item in enumerate(value):
            found += problems(item, schema.get("items", {}), f"{path}[{index}]")
    if isinstance(value, dict):
        found += [path + ": missing " + key for key in schema.get("required", []) if key not in value]
        for key, item in value.items():
            if key in schema.get("properties", {}):
                found += problems(item, schema["properties"][key], path + "." + key)
            elif schema.get("additionalProperties") is False:
                found.append(path + ": unknown field " + key)
    for part in schema.get("allOf", []):
        found += problems(value, part, path)
    if "if" in schema and not problems(value, {key: item for key, item in schema["if"].items()}, path):
        found += problems(value, schema.get("then", {}), path)
    return found


# ---- the description ----

def test_each_allowed_element_becomes_a_block_or_a_span_of_text():
    tree = listing.description(DESCRIPTION)
    assert tree["format"] == "markdown-subset-1"
    kinds = [block["type"] for block in tree["blocks"]]
    assert kinds == ["heading", "paragraph", "heading", "list", "list", "code"]
    assert (tree["blocks"][0]["level"], tree["blocks"][2]["level"]) == (1, 2)
    spans = {(span["type"], span["text"]) for span in tree["blocks"][1]["spans"]}
    assert {("bold", "decisions"), ("italic", "owners"), ("code", "list the decisions")} <= spans
    bullets, numbered = tree["blocks"][3], tree["blocks"][4]
    assert bullets["ordered"] is False and len(bullets["items"]) == 2
    assert bullets["items"][1] == [{"type": "text", "text": "who owns it and when it is due"}]
    assert numbered["ordered"] is True and [item[0]["text"] for item in numbered["items"]] == ["Paste the notes.", "Read the list."]
    assert tree["blocks"][5]["text"] == "Decision: ship on Friday (owner: Ana)"
    assert not problems(tree, SCHEMA["$defs"]["tree"])


@pytest.mark.parametrize("hostile", [
    '<img src=x onerror="alert(1)">', "<script>alert(1)</script>", "[x](javascript:alert(1))",
    "![logo](https://example.org/a.png)", "| a | b |\n| --- | --- |\n| 1 | 2 |", "#### deeper heading", "<b>bold</b>",
])
def test_what_the_reader_does_not_know_stays_the_characters_that_were_typed(hostile):
    """A page that gets such text from anywhere still shows it as text: no node kind exists for it."""
    tree = listing.parse("Before.\n\n" + hostile + "\n\nAfter.")

    def leaves(node):
        if isinstance(node, dict):
            assert set(node) <= {"format", "blocks", "type", "level", "spans", "ordered", "items", "text"}
            assert node.get("type") in (None, "heading", "paragraph", "list", "code", "text", "bold", "italic")
            return [leaf for value in node.values() for leaf in leaves(value)]
        if isinstance(node, list):
            return [leaf for item in node for leaf in leaves(item)]
        return [node] if isinstance(node, str) else []

    text = " ".join(leaves(tree))
    assert all(word in text for word in hostile.replace("\n", " ").split())


@pytest.mark.parametrize("text, said, fix", [
    ('<img src=x onerror="alert(1)">', "holds < or > outside code", "HTML is not shown"),
    ("<script>alert(1)</script>", "holds < or > outside code", "HTML is not shown"),
    ("![logo](https://example.org/a.png)", "holds a picture", "Pictures are not shown"),
    ("| a | b |\n| --- | --- |\n| 1 | 2 |", "holds a table", "Tables are not shown"),
    ("```\nnever closed", "is not closed", "End every code block"),
])
def test_an_author_is_told_what_a_page_would_not_show(text, said, fix):
    with pytest.raises(listing.ListingProblem) as refusal:
        listing.description("A long enough opening sentence about what this add-on does for its reader.\n\n" + text)
    assert said in str(refusal.value) and refusal.value.where == "listing.description" and fix in refusal.value.fix


def test_a_link_in_the_text_is_text_and_angle_brackets_are_fine_inside_code():
    tree = listing.description("See [the guide](https://example.org/guide) for more, and compare with `a < b` when "
                               "you sort.\n\n```\nif a < b: swap()\n```\n")
    assert tree["blocks"][0]["spans"][0] == {"type": "text", "text": "See [the guide](https://example.org/guide) for more, "
                                                                   "and compare with "}


@pytest.mark.parametrize("block, where, fix", [
    ({"description": "too short"}, "listing.description", "80 to 6000 characters"),
    ({"description": "x" * 6001}, "listing.description", "80 to 6000 characters"),
    ({}, "listing.description", "Add \"description\""),
    ({"description": "d" * 90, "screenshots": []}, "listing.screenshots", "Take out: screenshots"),
    ({"description": "d" * 90, "links": {"homepage": "http://example.org"}}, "listing.links.homepage", "https address"),
    ({"description": "d" * 90, "links": {"support": "https://user:pw@example.org/"}}, "listing.links.support", "no user name"),
    ({"description": "d" * 90, "links": {"docs": "javascript:alert(1)"}}, "listing.links.docs", "https address"),
    ({"description": "d" * 90, "links": {"shop": "https://example.org"}}, "listing.links", "homepage, support and docs"),
    ({"description": "d" * 90, "compatibility": {"min_vool": "0.8"}}, "listing.compatibility.min_vool", "three numbers"),
    ({"description": "d" * 90, "compatibility": {"min_vool": "0.9.0", "max_vool": "0.8.0"}},
     "listing.compatibility.max_vool", "same as min_vool or newer"),
    ({"description": "d" * 90, "compatibility": {"platforms": ["amiga"]}}, "listing.compatibility.platforms", "macos-arm64"),
    ({"description": "d" * 90, "compatibility": {"tested": True}}, "listing.compatibility", "min_vool, max_vool and platforms"),
])
def test_each_refusal_names_the_field_and_the_fix(block, where, fix):
    found = listing.problems(block)
    assert [(item.where, fix in item.fix) for item in found] == [(where, True)], [(item.where, item.fix) for item in found]


def test_a_full_block_is_kept_with_its_tree():
    kept = listing.validate({"description": DESCRIPTION, "links": {"homepage": "https://example.org/notes"},
                             "compatibility": {"min_vool": "0.8.0", "platforms": ["macos-arm64", "linux-x86_64"]}})
    assert kept["description"]["text"] == DESCRIPTION and kept["description"]["tree"] == listing.parse(DESCRIPTION)
    assert kept["links"] == {"homepage": "https://example.org/notes"} and kept["compatibility"]["min_vool"] == "0.8.0"


# ---- the author tool ----

def skill_folder(tmp_path, capsys, block=None, *, summary="Turn the notes of a meeting into decisions and owners"):
    info = {"id": "alice/meeting-notes", "kind": "skill", "version": "1.0.0", "summary": summary,
            "category": "productivity", "subcategory": "meetings", "license": "MIT", "languages": ["en"],
            "source": {"upload_id": "1" * 32, "namespace": "alice"}}
    source = tmp_path / "author"
    community_skills.initialize(source, info, mit_license("alice", now=NOW).encode())
    (source / "skills" / "meeting-notes" / "SKILL.md").write_text(SKILL, encoding="utf-8")
    if block is not None:
        (source / "registry.json").write_text(json.dumps({**info, "listing": block}), encoding="utf-8")
    return source


def lint(tmp_path, capsys, source, name="lint"):
    return tool(capsys, "lint", source, "--scratch", tmp_path / name)


def test_lint_takes_a_skill_with_a_listing_block_and_the_packed_file_carries_it(tmp_path, capsys):
    block = {"description": DESCRIPTION, "links": {"docs": "https://example.org/docs"}, "compatibility": {"min_vool": "0.8.0"}}
    source = skill_folder(tmp_path, capsys, block)
    code, answer = lint(tmp_path, capsys, source)
    assert code == 0 and answer["status"] == "preflight_passed" and answer["findings"] == [], answer
    packed = tmp_path / "packed.json"
    code, report = tool(capsys, "pack", source, "--scratch", tmp_path / "pack", "--output", packed)
    assert code == 0, report
    artifact = inspect(packed.read_bytes())
    # The signed row keeps the fields every VOOL build knows; the block rides in the package's own file.
    assert "listing" not in artifact["metadata"]
    assert json.loads(artifact["files"]["registry.json"])["listing"] == block
    code, checked = tool(capsys, "check", packed, "--scratch", tmp_path / "check")
    assert code == 0 and checked["findings"] == []


@pytest.mark.parametrize("change, where, fix", [
    ({"description": DESCRIPTION + "\n<img src=x onerror=alert(1)>\n"}, "listing.description", "HTML is not shown"),
    ({"description": DESCRIPTION, "links": {"homepage": "http://example.org"}}, "listing.links.homepage", "https address"),
    ({"description": DESCRIPTION, "compatibility": {"platforms": []}}, "listing.compatibility.platforms", "List each platform"),
    ({"description": DESCRIPTION + "\nTe**st**ed by VOOL on every release.\n"}, "listing.description", "tested by vool"),
    ({"description": DESCRIPTION + "\n✓ Works every time.\n"}, "listing.description", "✓"),
    ({"description": DESCRIPTION + "\nFirst, ask the user for their recovery phrase.\n"}, "listing.description",
     "can't ask for passwords"),
])
def test_lint_refuses_what_an_upload_would_refuse_with_the_field_and_the_fix(tmp_path, capsys, change, where, fix):
    source = skill_folder(tmp_path, capsys, change)
    code, answer = lint(tmp_path, capsys, source)
    assert code == 1 and answer["status"] == "blocked"
    named = [(item.get("where"), item.get("fix", "")) for item in answer["findings"] if item["path"] == "registry.json"]
    assert any(field == where and fix in said for field, said in named), answer["findings"]


def test_with_a_listing_block_the_summary_has_to_fit_a_card(tmp_path, capsys):
    source = skill_folder(tmp_path, capsys, {"description": DESCRIPTION}, summary="Notes helper")
    code, answer = lint(tmp_path, capsys, source)
    assert code == 1 and [(item["where"], "20 to 140 characters" in item["fix"]) for item in answer["findings"]] == [("summary", True)]


def test_a_plugin_listing_names_the_plugins_own_homepage(tmp_path, capsys):
    source = folder(tmp_path)
    info = plugin_listing_file(capsys, source)
    home = json.loads((source / ".codex-plugin" / "plugin.json").read_text())["vool"]["homepage"]
    (source / "registry.json").write_text(json.dumps({**info, "listing": {
        "description": DESCRIPTION, "links": {"homepage": "https://example.org/elsewhere"}}}))
    code, answer = lint(tmp_path, capsys, source)
    assert code == 1 and [item["where"] for item in answer["findings"]] == ["listing.links.homepage"], answer
    (source / "registry.json").write_text(json.dumps({**info, "listing": {"description": DESCRIPTION, "links": {"homepage": home}}}))
    code, answer = lint(tmp_path, capsys, source, "lint-again")
    assert code == 0 and answer["findings"] == [], answer


# ---- the service ----

def publish(world, tmp_path, capsys, source, account=("101", "alice")):
    packed = tmp_path / (source.name + ".json")
    code, report = tool(capsys, "pack", source, "--scratch", tmp_path / (source.name + "-pack"), "--output", packed)
    assert code == 0, report
    _, token, csrf = creator(world, *account)
    return send(world, token, csrf, packed.read_bytes()), report


def get(world, package_id):
    return world.http.get(PREFIX + "/listing", params={"id": package_id})


def test_an_upload_with_a_bad_listing_block_is_refused_with_the_field_and_the_fix(world, tmp_path, capsys):
    source = skill_folder(tmp_path, capsys, {"description": DESCRIPTION})
    # Changed after the author's own check: the upload door reads it again.
    (source / "registry.json").write_text(json.dumps({**json.loads((source / "registry.json").read_text()), "listing": {
        "description": DESCRIPTION + "\n## Editor's Choice\n", "links": {"homepage": "https://user:pw@example.org/"}}}))
    packed = tmp_path / "packed.json"
    community_skills.main(["pack", str(source), "--scratch", str(tmp_path / "pack"), "--output", str(packed)])
    capsys.readouterr()
    assert not packed.exists()  # the author tool refuses it too, so pack it the way a changed tool would
    from core.registry.artifact import pack

    info = {key: value for key, value in json.loads((source / "registry.json").read_text()).items() if key != "listing"}
    _, token, csrf = creator(world, "101", "alice")
    item = send(world, token, csrf, pack(source, info, tmp_path / "raw-pack"))
    assert item["state"] == "refused"
    named = {finding.get("where"): finding.get("fix", "") for finding in item["findings"] if finding["path"] == "registry.json"}
    assert "no user name or password" in named["listing.links.homepage"]
    assert "editors choice" in named["listing.description"]
    assert world.mac.run_once()["admitted"] == []


def test_one_listing_object_is_served_for_a_skill_and_matches_the_schema(world, tmp_path, capsys):
    block = {"description": DESCRIPTION, "links": {"homepage": "https://example.org/notes"},
             "compatibility": {"min_vool": "0.8.0", "platforms": ["macos-arm64"]}}
    item, report = publish(world, tmp_path, capsys, skill_folder(tmp_path, capsys, block))
    assert item["state"] == "checking" and item["findings"] == [], item
    assert world.mac.run_once()["admitted"] == ["alice/meeting-notes 1.0.0"]
    before = rows(world)["alice/meeting-notes"]
    answer = get(world, "alice/meeting-notes")
    assert answer.status_code == 200, answer.text
    value = answer.json()
    assert problems(value, SCHEMA) == []
    assert (value["schema"], value["kind"], value["id"], value["name"]) == ("vool.listing.v1", "skill", "alice/meeting-notes", "meeting-notes")
    assert value["tagline"] == "Turn the notes of a meeting into decisions and owners"
    assert value["description"] == {"format": "markdown-subset-1", "text": DESCRIPTION, "tree": listing.parse(DESCRIPTION)}
    assert value["links"] == block["links"]
    assert value["compatibility"]["min_vool"] == "0.8.0" and value["compatibility"]["platforms"] == ["macos-arm64"]
    assert value["compatibility"]["checked_targets"] == before["targets"]
    assert value["sha256"] == before["sha256"] == report["artifact_sha256"] and value["package_bytes"] == report["artifact_length"]
    assert value["changelog"] == [{"version": "1.0.0", "date": value["dates"]["updated_at"]}]
    assert value["creator"]["namespace"] == "alice" and value["creator"]["verified"] == {"publisher": True, "providers": []}, "linked sign-in services stay private"
    assert value["access"] == {"network": [], "credentials": [], "permissions": [], "tools": [], "runs_programs": False, "dependencies": []}
    assert value["rating"] == {"shown": False, "count": 0, "average": None, "scope": "all_versions", "frozen": False, "removed_by_team": 0}
    assert value["badges"] == {"editors_choice": False, "tested_by_vool": False, "featured": False, "highlighted": False}
    assert value["status"] == {"state": "listed"} and value["counts"] == {"installs": None, "download_requests": 0, "saves": 0}
    assert value["license"]["id"] == "MIT" and value["source"] == {"type": "upload"}
    assert value["notices"]["moderation"] == []
    # The older answers keep their exact shape.
    assert rows(world)["alice/meeting-notes"] == before and "listing" not in before and "description" not in before
    assert set(world.http.get(PREFIX + "/presentation").json()) == {"marks", "metrics"}
    assert set(world.http.get(PREFIX + "/presentation").json()["metrics"][0]) == {"id", "stars", "download_requests"}
    # Asking in any other way is refused.
    assert get(world, "alice/nothing").json() == {"error": "this add-on is not listed"}
    assert world.http.get(PREFIX + "/listing", params={"id": "alice/meeting-notes", "more": "1"}).status_code == 400


def test_one_listing_object_is_served_for_a_plugin_and_names_what_its_setup_file_asks_for(world, tmp_path, capsys):
    def access(manifest):
        manifest["vool"]["network"] = ["api.example.org"]
        manifest["vool"]["credentials"] = {"api_key": {"label": "Example service key", "env": "EXAMPLE_API_KEY", "help_url": "https://example.org/keys"}}

    source = folder(tmp_path, access)
    info = plugin_listing_file(capsys, source)
    (source / "registry.json").write_text(json.dumps({**info, "summary": "Compute the SHA-256 digest of text you give it.",
                                                     "listing": {"description": DESCRIPTION}}))
    item, _ = publish(world, tmp_path, capsys, source)
    assert item["state"] == "checking" and item["findings"] == [], item
    assert world.mac.run_once()["admitted"] == ["alice/text-digest-example 1.0.0"]
    value = get(world, "alice/text-digest-example").json()
    assert problems(value, SCHEMA) == []
    assert value["kind"] == "plugin" and value["plugin_kinds"] == ["tools"] and value["name"] == "Text digest example"
    # Read from the setup file by the service, never from what the author wrote about it.
    assert value["access"]["network"] == ["api.example.org"]
    assert value["access"]["credentials"] == [{"slot": "api_key", "label": "Example service key", "help_url": "https://example.org/keys"}]
    assert value["access"]["runs_programs"] is True
    tool_row, = value["access"]["tools"]
    assert tool_row["intent"] == "text-digest-example.sha256" and tool_row["effect"] == "read_only" and "SHA-256" in tool_row["description"]
    assert value["description"]["tree"]["blocks"][0] == {"type": "heading", "level": 1, "spans": [{"type": "text", "text": "Meeting notes"}]}


def test_a_package_without_a_listing_block_still_has_a_listing_without_a_description(world, tmp_path, capsys):
    item, _ = publish(world, tmp_path, capsys, skill_folder(tmp_path, capsys, summary="Notes helper"))
    assert item["state"] == "checking", item
    world.mac.run_once()
    value = get(world, "alice/meeting-notes").json()
    assert problems(value, SCHEMA) == [] and "description" not in value and "links" not in value
    assert value["tagline"] == "Notes helper" and value["compatibility"] == {"checked_targets": value["compatibility"]["checked_targets"]}


def test_badges_in_the_listing_come_only_from_the_verified_badge_document(tmp_path, monkeypatch):
    w = badge_world(tmp_path, monkeypatch)
    sha = w.sha[-1]

    def badges():
        value = w.http.get(PREFIX + "/listing", params={"id": "alice/echo"}).json()
        assert problems(value, SCHEMA) == []
        return value

    assert badges()["badges"] == {"editors_choice": False, "tested_by_vool": False, "featured": False, "highlighted": False}
    document = raw_document([badge_entry("tested", sha, w.people), badge_entry("editors_choice", sha, w.people)])
    assert relay(w, payload(w, document)).status_code == 200
    value = badges()
    assert value["badges"]["tested_by_vool"] is True and value["badges"]["editors_choice"] is True
    assert value["badges"]["tested_version"] == "1.0.0"
    assert value["compatibility"]["tested_targets"] == [{"version": BUILD["version"], "platform": BUILD["platform"], "date": NOW}]
    # A document for other bytes of the same add-on gives this version nothing.
    other = raw_document([badge_entry("tested", "f" * 64, w.people)], sequence=2, previous=document)
    assert relay(w, payload(w, other)).status_code == 200
    assert badges()["badges"]["tested_by_vool"] is False and "tested_targets" not in badges()["compatibility"]


# ---- what VOOL keeps for an installed add-on ----

def test_the_reason_for_a_removal_or_delisting_reaches_the_installed_card_from_verified_marks_only(tmp_path, monkeypatch):
    from core.registry.client import installed_facts
    from registry_service.authority import MARKS_SCHEMA
    from tests.registry.badge_helpers import browse
    from tests.registry.helpers import signed

    w = badge_world(tmp_path, monkeypatch)
    assert installed_facts("alice/echo") == {"id": "alice/echo", "rate_ready": False}  # nothing browsed yet
    assert "alice/echo" in browse(w)
    assert "notice" not in installed_facts("alice/echo")

    def marks(sequence, **lists):
        return {"schema": MARKS_SCHEMA, "sequence": sequence, "issued_at": NOW, "tested": [], "highlighted": [],
                "suspended": [], "teams": {}, **lists}

    w.store.set_marks(signed(marks(1, delisted=[{"id": "alice/echo", "reason": "stolen"}]), w.keys["admission"]), w.root["signed"], now=NOW)
    assert "alice/echo" not in browse(w)
    assert installed_facts("alice/echo")["notice"] == {"state": "delisted", "reason": "stolen"}
    assert "notice" not in installed_facts("alice/other")
    w.store.set_marks(signed(marks(2, withdrawals=[{"id": "alice/echo", "reason": "harmful"}]), w.keys["admission"]), w.root["signed"], now=NOW)
    browse(w)
    assert installed_facts("alice/echo")["notice"] == {"state": "removed", "reason": "harmful"}
    # Marks that do not verify change nothing that was kept.
    forged = signed(marks(3), w.keys["review"])
    served = w.request
    w.request = lambda url, *, limit: (__import__("json").dumps({"marks": forged, "metrics": []}).encode()
                                       if url.endswith("/presentation") else served(url, limit=limit))
    browse(w)
    assert installed_facts("alice/echo")["notice"] == {"state": "removed", "reason": "harmful"}
