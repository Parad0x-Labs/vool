"""Declarative plugin metadata, validated before any package can acquire authority."""
from __future__ import annotations

import ipaddress
import re
from copy import deepcopy
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from core import json_schema_lite
from core.plugin_paths import inside

IDENTITY = r"^[a-z][a-z0-9_-]{0,79}$"
SEMVER = r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
ENV_NAME = r"^[A-Z][A-Z0-9_]{0,95}$"
# A tool's name as the model reads it: the plugin's id, one dot, a short name. And the name its own
# connection server knows the tool by. Both are written into lines VOOL builds (the tool list, the
# line above a tool's output), so neither may hold a space, a quote, a bracket or a line break.
INTENT = r"^[a-z][a-z0-9_-]{0,79}\.[a-z0-9_]{1,64}$"
SERVER_TOOL = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"
MONEY = re.compile(r"(?:^|[._-])(?:wallet|payment|payments|sign|signing|approve|approval|cap|caps|budget|receipt|receipts)(?:$|[._-])", re.I)
PAID = frozenset({"financial_or_paid_actions", "wallet_spend", "credit_spend"})
_RESERVED_ENV = frozenset({"PATH", "HOME", "TMPDIR", "SHELL", "ENV", "BASH_ENV", "SSL_CERT_FILE",
                          "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "HTTPS_PROXY", "HTTP_PROXY",
                          "ALL_PROXY", "NO_PROXY", "SSH_AUTH_SOCK", "GPG_AGENT_INFO"})


SETUP = "This plugin has a setup mistake its maker needs to fix. It was not installed."
WEBSITES = "This plugin lists the websites it uses in a way VOOL doesn't accept. Ask its maker to fix it."
OUTSIDE = "This plugin tries to run something outside its own files, so VOOL won't start it."
NO_MONEY = "Plugins can't spend money, use your wallet or approve actions for you."
PAYS = "This plugin lists the services it pays in a way VOOL doesn't accept. Ask its maker to fix it."
_MONEY_WORDS = "wallet, payment, sign, approve, cap, budget or receipt"


class ManifestRefusal(ValueError):
    """A refusal with two readers.

    ``str()`` is the sentence the person installing the plugin reads; those sentences are catalogued
    and translated, and one sentence covers many mistakes. ``where`` and ``fix`` are for the plugin's
    maker: the manifest field at fault and what to write instead. Without them a maker saw "a setup
    mistake" for a wildcard host, a missing key slot and a misnamed tool alike, and could only guess.
    They are VOOL's own words and quote at most a short, flattened piece of the value.
    """

    def __init__(self, message: str, *, where: str = "", fix: str = ""):
        super().__init__(message)
        self.where, self.fix = where, fix


def _no(message: str, where: str = "", fix: str = "") -> None:
    raise ManifestRefusal(message, where=where, fix=fix)


def _shown(value, limit: int = 60) -> str:
    """A short, one-line piece of a value for the maker's note; never more than `limit` characters."""
    text = "".join(char for char in " ".join(str(value).split()) if char.isprintable() and char not in "\"'`<>")
    return "'" + text[:limit] + "'"


def credential_env_name(name: str, where: str = "") -> None:
    if not isinstance(name, str):
        _no("A plugin credential environment name must be text.", where, "Write the env name as text, such as ACME_API_KEY.")
    if (name in _RESERVED_ENV or name.startswith(("VOOL_", "NULLA_", "PYTHON", "LD_", "DYLD_", "NODE_", "RUBY"))
            or re.match(r"^(OPENAI|ANTHROPIC|OPENROUTER|GROQ|GEMINI|GOOGLE|MISTRAL|DEEPSEEK|CLOUDFLARE|USEPOD)_", name)):
        _no("A plugin may not ask for owner model keys, and it can't change settings for other programs. It was not installed.",
            where, f"{_shown(name)} is kept back. Pick a name of your own for the key, such as ACME_API_KEY. Not accepted: "
            "the owner's model keys (OPENAI_, ANTHROPIC_, GOOGLE_ ...), VOOL's own (VOOL_), system names (PATH, HOME, "
            "HTTPS_PROXY ...) and language runtime names (PYTHON..., NODE_..., LD_..., DYLD_...).")


def _text(limit=500):
    return {"type": "string", "minLength": 1, "maxLength": limit}


def _object(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


CREDENTIAL = _object({"label": _text(120), "help_url": _text(2000), "env": {**_text(96), "pattern": ENV_NAME}}, ("label", "help_url", "env"))
SERVER = _object({"command": _text(), "args": {"type": "array", "maxItems": 64, "items": {"type": "string", "maxLength": 2000}}, "env": {"type": "object", "propertyNames": {"pattern": ENV_NAME}, "additionalProperties": _text(100)}}, ("command",))
HANDLER = _object({"kind": {"enum": ["subprocess", "mcp"]}, "entry": _text(), "args": {"type": "array", "items": {"type": "string"}, "maxItems": 64}, "timeout_seconds": {"type": "number", "minimum": 1, "maximum": 300}, "network": {"type": "boolean"}, "env_allowlist": {"type": "array", "items": {"type": "string", "pattern": ENV_NAME}}, "server": {**_text(), "pattern": IDENTITY}, "tool": {**_text(64), "pattern": SERVER_TOOL}}, ("kind",))
TOOL = _object({"intent": {**_text(145), "pattern": INTENT}, "description": {**_text(2000), "minLength": 12}, "handler": HANDLER, "input_schema": {"type": "object"}, "side_effect_class": _text(), "approval_requirement": _text(), "permission_actions": {"type": "array", "items": _text(100)}, "claim": {"type": "object"}, "mutation": {"type": "object"}, "trust": _object({"side_effect_class": _text(), "permission_actions": {"type": "array", "items": _text(100)}}, ("side_effect_class", "permission_actions"))}, ("intent", "description", "handler", "input_schema"))
PROVIDER = _object({"base_url": _text(2000), "auth_placement": {"enum": ["header", "path"]}, "credential_slot": {**_text(80), "pattern": IDENTITY}, "auth_path": _text(500), "model_list_path": _text(500), "pricing_source": _text(2000), "api_path": _text(500), "x402": {"type": "boolean"}}, ("base_url", "auth_placement", "credential_slot", "model_list_path", "pricing_source"))
SCANNER = _object({"base_url": _text(2000), "credential_slot": {**_text(80), "pattern": IDENTITY}, "test_path": _text(500), "scan_path": _text(500), "input_template": {"type": "object"}, "response_schema": {"type": "object"}, "response_map": {"type": "object", "additionalProperties": _text(200)}}, ("base_url", "credential_slot", "test_path", "scan_path", "input_template", "response_schema", "response_map"))
VOOL = _object({"id": {**_text(80), "pattern": IDENTITY}, "version": {**_text(100), "pattern": SEMVER}, "name": _text(120), "publisher": _text(120), "description": _text(2000), "homepage": _text(2000), "license": _text(100), "kind": {"type": "array", "minItems": 1, "maxItems": 3, "items": {"enum": ["tools", "model_provider", "scanner"]}}, "tools": {"type": "array", "maxItems": 128, "items": TOOL}, "mcpServers": {"type": "object", "propertyNames": {"pattern": IDENTITY}, "additionalProperties": SERVER}, "network": {"type": "array", "maxItems": 32, "items": _text(253)}, "credentials": {"type": "object", "propertyNames": {"pattern": IDENTITY}, "additionalProperties": CREDENTIAL}, "model_provider": PROVIDER, "scanner": SCANNER, "paid_services": {"type": "array", "maxItems": 8, "items": _text(2000)}}, ("id", "version", "name", "publisher", "description", "homepage", "license", "kind", "network", "credentials"))
LEGACY_PROVENANCE = _object({"origin": {"type": "string"}, "staged_by": {"type": "string"}})
MANIFEST_SCHEMA = {"$schema": "https://json-schema.org/draft/2020-12/schema", "title": "Plugin package manifest", "type": "object", "required": ["name"], "properties": {"name": _text(200), "version": {"type": "string"}, "vool": {"oneOf": [VOOL, LEGACY_PROVENANCE]}}}


def validate_schema(value, schema, path="manifest"):
    """The schema's bounded subset; reuse the runtime's primitive validators."""
    if "oneOf" in schema:
        matches = sum(not validate_schema(value, candidate, path) for candidate in schema["oneOf"])
        if matches == 1:
            return []
        if isinstance(value, dict) and "id" in value and VOOL in schema["oneOf"]:
            # A block with an id is meant as the access block: say what is wrong inside it. "Must match
            # exactly one supported schema" was all a maker saw for one misspelt field.
            return validate_schema(value, VOOL, path) or [f"{path}: must match exactly one supported schema"]
        return [f"{path}: must match exactly one supported schema"]
    primitive = {k: v for k, v in schema.items() if k in json_schema_lite._SUPPORTED_KEYWORDS}
    # Recurse ourselves so additional-property schemas are enforced too.
    primitive.pop("properties", None)
    primitive.pop("items", None)
    primitive.pop("required", None)
    primitive.pop("additionalProperties", None)
    errors = json_schema_lite.validate(value, primitive, path=path)
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing {key}")
        for key, item in value.items():
            if "propertyNames" in schema:
                errors.extend(validate_schema(key, schema["propertyNames"], path + ".name"))
            if key in properties:
                errors.extend(validate_schema(item, properties[key], path + "." + str(key)))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unknown field {key}")
            elif isinstance(schema.get("additionalProperties"), dict):
                errors.extend(validate_schema(item, schema["additionalProperties"], path + "." + str(key)))
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            errors.extend(validate_schema(item, schema["items"], f"{path}[{index}]"))
    return errors


def whole_match(pattern: str, value) -> bool:
    """Whether the whole value has the shape. The schema check searches, and a search for "$" also
    stops before a line break at the very end; a tool name ending in one must not pass."""
    return isinstance(value, str) and re.fullmatch(pattern[1:-1], value) is not None


def host_name(value: str, where: str = "") -> str:
    """An exact public DNS name, never a URL, wildcard, address or local name."""
    if not isinstance(value, str) or value != value.lower() or len(value) > 253:
        _no(WEBSITES, where, "Write the host name in lower case, at most 253 characters, for example api.example.com.")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        pass
    else:
        _no(WEBSITES, where, f"{_shown(value)} is an address in numbers. List the service's host name instead.")
    labels = value.split(".")
    if len(labels) < 2 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels):
        _no(WEBSITES, where, f"{_shown(value)} is not one exact host name. List each host by itself, such as "
            "api.example.com: no wildcard (*), no https://, no path, no port, no spaces, at least one dot.")
    if labels[-1] in {"local", "localhost", "internal", "lan", "home", "invalid"} or value.endswith(".localhost"):
        _no(WEBSITES, where, f"{_shown(value)} is a name on the local network. A plugin reaches public services only "
            "(not .local, .localhost, .internal, .lan, .home or .invalid).")
    # DNS spellings that operating systems interpret as abbreviated numeric addresses.
    if not re.search(r"[a-z]", labels[-1]) or all(re.fullmatch(r"(?:0x[0-9a-f]+|[0-9]+)", label) for label in labels):
        _no(WEBSITES, where, f"{_shown(value)} reads as an address in numbers. List the service's host name instead.")
    return value


def https_url(value: str, hosts=None, where: str = ""):
    plain = "Write a full https:// address with a plain public host name: no user name or password, no # part, port 443 only."
    try:
        parsed = urlsplit(value)
        valid = parsed.scheme == "https" and not parsed.username and not parsed.password and not parsed.fragment and parsed.port in (None, 443)
        host = host_name(parsed.hostname or "")
    except (ValueError, TypeError):
        raise ManifestRefusal(WEBSITES, where=where, fix=f"{_shown(value)} is not accepted. {plain}") from None
    if not valid or (hosts is not None and host not in hosts):
        _no("The plugin tried to reach a website it didn't list. VOOL stopped it.", where,
            f"{_shown(value)} is not accepted. {plain}" if not valid else
            f"The host {_shown(host)} is not in vool.network. Add it there, or use a host that is listed.")
    return parsed


def package_command(value: str, root: Path, where: str = "") -> Path:
    text = str(value)
    relative = PurePosixPath(text)
    if not text or relative.is_absolute() or ".." in relative.parts or "\\" in text or "\x00" in text or ":" in text or "$" in text:
        _no(OUTSIDE, where, f"{_shown(text)} is not a path inside the plugin folder. Give one such as bin/tool: not empty, "
            "not absolute, no .., no backslash, colon or $.")
    if relative.name in {"npx", "uvx", "pip", "pip3", "npm", "curl", "wget"}:
        _no(OUTSIDE, where, f"{_shown(relative.name)} is not started: VOOL runs no installer or downloader for a plugin. "
            "Ship the program and everything it needs inside the plugin folder.")
    try:
        result = inside(root, text)
    except ValueError:
        raise ManifestRefusal(OUTSIDE, where=where, fix=f"{_shown(text)} leaves the plugin folder (a link, or a path "
                              "that climbs out). Point at a real file inside the folder.") from None
    return result


def permission_wall(actions, where: str = ""):
    if not isinstance(actions, (list, tuple)) or any(not isinstance(item, str) for item in actions):
        _no("Permission actions must be a list of names.", where, "Write the permission actions as a list of names.")
    for item in actions:
        if item in PAID or MONEY.search(re.sub(r"([a-z])([A-Z])", r"\1_\2", item)):
            _no("Plugins cannot request wallet, payment, signing, approval, cap, budget or receipt authority.", where,
                f"Remove {_shown(item)}. A plugin holds no wallet and approves nothing. To have VOOL pay a service for "
                "the plugin, list the service in vool.paid_services; the owner ticks it and sets the limits.")


def _flags(value, where: str = "manifest"):
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                _no("Manifest field names must be strings.", where, "Write every field name as text.")
            at = f"{where}.{key}"
            normalized = re.sub(r"([a-z])([A-Z])", r"\1_\2", key).lower().replace("-", "_")
            if normalized in {"auto_enable", "auto_start", "autostart", "owner_credentials", "owner_keys", "owner_paid_keys", "owner_model_keys"}:
                _no("Plugins cannot start automatically or request owner credentials.", at,
                    "Remove the field. A plugin runs only when one of its tools is called after the owner switched it on, "
                    "and it never receives the owner's own keys; declare the plugin's own key in vool.credentials.")
            if key.lower() in {"wallet", "payments", "signing", "approval", "caps", "budget", "receipts"}:
                _no(NO_MONEY, at, "Remove the field. A plugin holds no wallet, signs nothing and approves nothing. For paid "
                    "calls, list the service in vool.paid_services; VOOL pays inside limits the owner sets.")
            _flags(item, at)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _flags(item, f"{where}[{index}]")


def paid_services(extension: dict, hosts) -> list[str]:
    """The services a plugin may ask VOOL to pay for it, as https origins inside its own listed websites.

    A declaration, never authority: the plugin holds no key, signs nothing and approves nothing. VOOL pays only from
    the Skills & plugins wallet, only after the owner ticked this plugin, only inside the limits the owner set for it
    (``core.wallet.addon_wallet``), and only on the owner's own turn at this computer."""
    listed = extension.get("paid_services") or []
    if listed and "tools" not in extension.get("kind", []):
        _no("Only a plugin with tools can ask VOOL to pay a service for it. It was not installed.", "vool.paid_services",
            'Add "tools" to vool.kind and declare the tools, or remove vool.paid_services.')
    origins: list[str] = []
    for index, entry in enumerate(listed):
        at = f"vool.paid_services[{index}]"
        parsed = https_url(entry, hosts, at)
        if parsed.query or parsed.path not in ("", "/"):
            _no(PAYS, at, f"{_shown(entry)} has a path or a ? part. List the service as https://host only.")
        origin = f"https://{parsed.hostname}"
        if origin in origins:
            _no(PAYS, at, f"{_shown(origin)} is listed twice. List each service once.")
        origins.append(origin)
    return origins


def _in_words(error: str, raw) -> str:
    """A schema error for the maker: a shape it names by its pattern is said in words instead."""
    name = raw.get("name") if isinstance(raw, dict) and isinstance(raw.get("name"), str) else "<plugin id>"
    shapes = {
        INTENT: f"is not a tool name: write {_shown(name)[1:-1]}.<tool>, the tool part in lowercase letters, digits and _",
        IDENTITY: "must be a short id: a lowercase letter, then lowercase letters, digits, _ or -",
        SEMVER: "must be a version such as 1.0.0",
        ENV_NAME: "must be a name in capitals, digits and _, such as ACME_API_KEY",
        SERVER_TOOL: "may hold letters, digits, dot, _ and - only",
    }
    for pattern, words in shapes.items():
        if repr(pattern) in error or pattern in error:
            return words
    return error


def validate_manifest(raw: dict, *, root: Path) -> dict:
    errors = validate_schema(raw, MANIFEST_SCHEMA)
    if errors:
        _no("; ".join(errors[:8]), errors[0].split(":", 1)[0],
            "; ".join(_in_words(error.split(": ", 1)[-1], raw) for error in errors[:8]) + ". The published schema is "
            "docs/reference/plugin-manifest.schema.json.")
    _flags(raw)
    permission_wall(raw.get("permissions", []), "permissions")
    for index, binding in enumerate(raw.get("credential_bindings", [])):
        at = f"credential_bindings[{index}]"
        if not isinstance(binding, str) or binding.startswith(("llm.cloud.", "wallet.", "signing.", "owner.")):
            _no("A plugin can't hold credentials that belong to you or to another plugin.", at,
                "Remove it. A plugin reads only its own key slots, declared in vool.credentials.")
        if str(binding).startswith("plugin.") and not str(binding).startswith("plugin." + raw["name"] + "."):
            _no("A plugin cannot reference another plugin's credentials.", at,
                f"Use only names that start with plugin.{raw['name']}. (this plugin's own slots).")
    extension = raw.get("vool", {})
    tools = extension.get("tools", raw.get("tools", [])) or []
    listed = "vool.tools" if "tools" in extension else "tools"
    if not isinstance(tools, list) or any(not isinstance(tool, dict) for tool in tools):
        _no("Plugin tools must be a list of tool declarations.", listed, "Write the tools as a list of objects.")
    for index, tool in enumerate(tools):
        at = f"{listed}[{index}]"
        permission_wall(tool.get("permission_actions", []), at + ".permission_actions")
        if tool.get("side_effect_class") in PAID:
            _no(NO_MONEY, at + ".side_effect_class", f"{_shown(tool.get('side_effect_class'))} is a spending class. "
                "Use read_only, workspace_write, network_send or network_publish.")
        if MONEY.search(tool.get("intent", "")):
            _no(NO_MONEY, at + ".intent", f"{_shown(tool.get('intent'))} names a money action. A tool name cannot hold "
                f"{_MONEY_WORDS} as a word of its own.")
        for name in tool.get("handler", {}).get("env_allowlist", []):
            credential_env_name(name, at + ".handler.env_allowlist")
    if not extension or "id" not in extension:
        return {}
    if extension["id"] != raw["name"] or extension["version"] != raw.get("version"):
        _no("The plugin's name and version must match its package.", "vool.id / vool.version",
            "vool.id must equal the top-level name, and vool.version must equal the top-level version.")
    if len(set(extension["kind"])) != len(extension["kind"]):
        _no(SETUP, "vool.kind", "List each kind once.")
    https_url(extension["homepage"], None, "vool.homepage")
    hosts = extension["network"]
    if len(set(hosts)) != len(hosts):
        _no(WEBSITES, "vool.network", "List each host once.")
    for index, host in enumerate(hosts):
        host_name(host, f"vool.network[{index}]")
    credentials = extension["credentials"]
    env = [row["env"] for row in credentials.values()]
    if len(set(env)) != len(env):
        _no("Each service key needs its own name. It was not installed.", "vool.credentials",
            "Two key slots use the same env name. Give each slot its own.")
    for slot, row in credentials.items():
        credential_env_name(row["env"], f"vool.credentials.{slot}.env")
        https_url(row["help_url"], None, f"vool.credentials.{slot}.help_url")
    servers = extension.get("mcpServers", {})
    if servers and "tools" not in extension["kind"]:
        _no(SETUP, "vool.kind", 'vool.mcpServers is declared, so vool.kind must list "tools". Add it, or remove the servers.')
    for name, server in servers.items():
        at = f"vool.mcpServers.{name}"
        package_command(server["command"], root, at + ".command")
        if any(key not in env or value != "${" + key + "}" for key, value in server.get("env", {}).items()):
            _no(SETUP, at + ".env", 'Each entry must read NAME: "${NAME}", where NAME is the env of a slot in '
                "vool.credentials. Nothing else is passed to a server: no fixed values and no other variables.")
        if any("${" in arg or "\x00" in arg for arg in server.get("args", [])):
            _no(SETUP, at + ".args", "Arguments are passed exactly as written. Take out ${...}; a key reaches the server "
                "through its env, never through an argument.")
    permission_wall(raw.get("permissions", []), "permissions")
    tools = extension.get("tools", raw.get("tools", []))
    if raw.get("tools") is not None and extension.get("tools") is not None and raw["tools"] != extension["tools"]:
        _no(SETUP, "tools / vool.tools", "The two tool lists differ. Declare the tools once, in vool.tools.")
    if tools and "tools" not in extension["kind"]:
        _no(SETUP, "vool.kind", 'Tools are declared, so vool.kind must list "tools".')
    for index, tool in enumerate(tools):
        at = f"{listed}[{index}]"
        permission_wall(tool.get("permission_actions", []), at + ".permission_actions")
        if tool.get("side_effect_class") in PAID:
            _no(NO_MONEY, at + ".side_effect_class", f"{_shown(tool.get('side_effect_class'))} is a spending class. "
                "Use read_only, workspace_write, network_send or network_publish.")
        handler = tool["handler"]
        if not whole_match(INTENT, tool["intent"]):
            _no(SETUP, at + ".intent", f"{_shown(tool['intent'])} is not a tool name. Write {extension['id']}.<tool>, "
                "the tool part in lowercase letters, digits and _ (at most 64), with nothing before or after.")
        if "tool" in handler and not whole_match(SERVER_TOOL, handler["tool"]):
            _no(SETUP, at + ".handler.tool", f"{_shown(handler['tool'])} is not accepted as the server's tool name. "
                "Use letters, digits, dot, _ and - only, at most 64.")
        if handler["kind"] == "subprocess":
            package_command(handler.get("entry", ""), root, at + ".handler.entry")
            if set(handler.get("env_allowlist", [])) - set(env):
                _no(SETUP, at + ".handler.env_allowlist", "It names a variable that is not the env of a slot in "
                    "vool.credentials. A tool receives the plugin's own keys only.")
            if handler.get("network") and not hosts:
                _no(WEBSITES, at + ".handler.network", "The tool asks for the internet but vool.network is empty. List "
                    "every host it reaches there; the owner sees that list before switching the plugin on.")
        else:
            if handler.get("server") not in servers or not handler.get("tool"):
                _no(SETUP, at + ".handler", "For kind mcp, set server to a name declared in vool.mcpServers and tool to "
                    "the name that server gives the tool.")
            if MONEY.search(handler["tool"]):
                _no("This plugin has a setup mistake its maker needs to fix: it may not offer money tools. It was not installed.",
                    at + ".handler.tool", f"{_shown(handler['tool'])} names a money action ({_MONEY_WORDS}). A plugin "
                    "offers no such tool.")
            if "trust" in tool:
                permission_wall(tool["trust"]["permission_actions"], at + ".trust.permission_actions")
                if tool["trust"]["side_effect_class"] in PAID:
                    _no(NO_MONEY, at + ".trust.side_effect_class", "A spending class is not accepted. Use read_only, "
                        "workspace_write, network_send or network_publish.")
    paid_services(extension, hosts)
    for kind in ("model_provider", "scanner"):
        block = extension.get(kind)
        if (kind in extension["kind"]) != (block is not None):
            _no(f"The {kind} declaration must match the plugin kind.", f"vool.{kind}",
                f'Declare vool.{kind} exactly when "{kind}" is in vool.kind: both or neither.')
        if block is None:
            continue
        base = https_url(block["base_url"], hosts, f"vool.{kind}.base_url")
        if base.query:
            _no(WEBSITES, f"vool.{kind}.base_url", "Take the ? part out of the base address.")
        if block["credential_slot"] not in credentials:
            _no("A service may read only its own declared credential slot.", f"vool.{kind}.credential_slot",
                f"{_shown(block['credential_slot'])} is not a slot in vool.credentials. Name one that is.")
        for key, value in block.items():
            if key.endswith("_path"):
                if not value.startswith("/") or value.startswith("//") or any(ch in value for ch in ("?", "#", "\\", "\x00")) or ".." in value:
                    _no(WEBSITES, f"vool.{kind}.{key}", f"{_shown(value)} is not a plain path. Start with one /, and use "
                        "no ?, #, backslash or ..")
        if kind == "model_provider":
            if block["auth_placement"] == "path" and block.get("auth_path", "").count("{credential}") != 1:
                _no(SETUP, "vool.model_provider.auth_path", 'With auth_placement "path", auth_path must hold {credential} '
                    "exactly once.")
            if block["auth_placement"] == "header" and "auth_path" in block:
                _no(SETUP, "vool.model_provider.auth_path", 'With auth_placement "header" there is no auth_path. Remove it.')
            pricing = block["pricing_source"]
            if pricing != "models":
                https_url(pricing, hosts, "vool.model_provider.pricing_source")
        else:
            json_schema_lite.assert_supported(block["response_schema"])
    return deepcopy(extension)
