"""Declarative plugin metadata, validated before any package can acquire authority."""
from __future__ import annotations

import ipaddress
import re
from copy import deepcopy
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from core import json_schema_lite

IDENTITY = r"^[a-z][a-z0-9_-]{0,79}$"
SEMVER = r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
ENV_NAME = r"^[A-Z][A-Z0-9_]{0,95}$"
MONEY = re.compile(r"(?:^|[._-])(?:wallet|payment|payments|sign|signing|approve|approval|cap|caps|budget|receipt|receipts)(?:$|[._-])", re.I)
PAID = frozenset({"financial_or_paid_actions", "wallet_spend", "credit_spend"})
_RESERVED_ENV = frozenset({"PATH", "HOME", "TMPDIR", "SHELL", "ENV", "BASH_ENV", "SSL_CERT_FILE",
                          "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "HTTPS_PROXY", "HTTP_PROXY",
                          "ALL_PROXY", "NO_PROXY", "SSH_AUTH_SOCK", "GPG_AGENT_INFO"})


def credential_env_name(name: str) -> None:
    if not isinstance(name, str):
        raise ValueError("A plugin credential environment name must be text.")
    if (name in _RESERVED_ENV or name.startswith(("VOOL_", "NULLA_", "PYTHON", "LD_", "DYLD_", "NODE_", "RUBY"))
            or re.match(r"^(OPENAI|ANTHROPIC|OPENROUTER|GROQ|GEMINI|GOOGLE|MISTRAL|DEEPSEEK|CLOUDFLARE|USEPOD)_", name)):
        raise ValueError("Plugin key slots cannot request owner model keys or change the process environment policy.")


def _text(limit=500):
    return {"type": "string", "minLength": 1, "maxLength": limit}


def _object(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


CREDENTIAL = _object({"label": _text(120), "help_url": _text(2000), "env": {**_text(96), "pattern": ENV_NAME}}, ("label", "help_url", "env"))
SERVER = _object({"command": _text(), "args": {"type": "array", "maxItems": 64, "items": {"type": "string", "maxLength": 2000}}, "env": {"type": "object", "propertyNames": {"pattern": ENV_NAME}, "additionalProperties": _text(100)}}, ("command",))
HANDLER = _object({"kind": {"enum": ["subprocess", "mcp"]}, "entry": _text(), "args": {"type": "array", "items": {"type": "string"}, "maxItems": 64}, "timeout_seconds": {"type": "number", "minimum": 1, "maximum": 300}, "network": {"type": "boolean"}, "env_allowlist": {"type": "array", "items": {"type": "string", "pattern": ENV_NAME}}, "server": {**_text(), "pattern": IDENTITY}, "tool": _text()}, ("kind",))
TOOL = _object({"intent": _text(), "description": {**_text(2000), "minLength": 12}, "handler": HANDLER, "input_schema": {"type": "object"}, "side_effect_class": _text(), "approval_requirement": _text(), "permission_actions": {"type": "array", "items": _text(100)}, "claim": {"type": "object"}, "mutation": {"type": "object"}, "trust": _object({"side_effect_class": _text(), "permission_actions": {"type": "array", "items": _text(100)}}, ("side_effect_class", "permission_actions"))}, ("intent", "description", "handler", "input_schema"))
PROVIDER = _object({"base_url": _text(2000), "auth_placement": {"enum": ["header", "path"]}, "credential_slot": {**_text(80), "pattern": IDENTITY}, "auth_path": _text(500), "model_list_path": _text(500), "pricing_source": _text(2000), "api_path": _text(500), "x402": {"type": "boolean"}}, ("base_url", "auth_placement", "credential_slot", "model_list_path", "pricing_source"))
SCANNER = _object({"base_url": _text(2000), "credential_slot": {**_text(80), "pattern": IDENTITY}, "test_path": _text(500), "scan_path": _text(500), "input_template": {"type": "object"}, "response_schema": {"type": "object"}, "response_map": {"type": "object", "additionalProperties": _text(200)}}, ("base_url", "credential_slot", "test_path", "scan_path", "input_template", "response_schema", "response_map"))
VOOL = _object({"id": {**_text(80), "pattern": IDENTITY}, "version": {**_text(100), "pattern": SEMVER}, "name": _text(120), "publisher": _text(120), "description": _text(2000), "homepage": _text(2000), "license": _text(100), "kind": {"type": "array", "minItems": 1, "maxItems": 3, "items": {"enum": ["tools", "model_provider", "scanner"]}}, "tools": {"type": "array", "maxItems": 128, "items": TOOL}, "mcpServers": {"type": "object", "propertyNames": {"pattern": IDENTITY}, "additionalProperties": SERVER}, "network": {"type": "array", "maxItems": 32, "items": _text(253)}, "credentials": {"type": "object", "propertyNames": {"pattern": IDENTITY}, "additionalProperties": CREDENTIAL}, "model_provider": PROVIDER, "scanner": SCANNER}, ("id", "version", "name", "publisher", "description", "homepage", "license", "kind", "network", "credentials"))
LEGACY_PROVENANCE = _object({"origin": {"type": "string"}, "staged_by": {"type": "string"}})
MANIFEST_SCHEMA = {"$schema": "https://json-schema.org/draft/2020-12/schema", "title": "Plugin package manifest", "type": "object", "required": ["name"], "properties": {"name": _text(200), "version": {"type": "string"}, "vool": {"oneOf": [VOOL, LEGACY_PROVENANCE]}}}


def validate_schema(value, schema, path="manifest"):
    """The schema's bounded subset; reuse the runtime's primitive validators."""
    if "oneOf" in schema:
        matches = sum(not validate_schema(value, candidate, path) for candidate in schema["oneOf"])
        return [] if matches == 1 else [f"{path}: must match exactly one supported schema"]
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


def host_name(value: str) -> str:
    """An exact public DNS name, never a URL, wildcard, address or local name."""
    if not isinstance(value, str) or value != value.lower() or len(value) > 253:
        raise ValueError("Network hosts must be exact lower-case HTTPS host names.")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        pass
    else:
        raise ValueError("IP addresses cannot be plugin network hosts.")
    labels = value.split(".")
    if len(labels) < 2 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels):
        raise ValueError("Network hosts cannot contain wildcards, ports or paths.")
    if labels[-1] in {"local", "localhost", "internal", "lan", "home", "invalid"} or value.endswith(".localhost"):
        raise ValueError("Local and private network hosts are not allowed.")
    # DNS spellings that operating systems interpret as abbreviated numeric addresses.
    if not re.search(r"[a-z]", labels[-1]) or all(re.fullmatch(r"(?:0x[0-9a-f]+|[0-9]+)", label) for label in labels):
        raise ValueError("Numeric network addresses are not allowed.")
    return value


def https_url(value: str, hosts=None):
    try:
        parsed = urlsplit(value)
        valid = parsed.scheme == "https" and not parsed.username and not parsed.password and not parsed.fragment and parsed.port in (None, 443)
        host = host_name(parsed.hostname or "")
    except (ValueError, TypeError):
        raise ValueError("A public HTTPS URL on port 443 is required.") from None
    if not valid or (hosts is not None and host not in hosts):
        raise ValueError("This HTTPS destination is not in the plugin network declaration.")
    return parsed


def package_command(value: str, root: Path) -> Path:
    text = str(value)
    relative = PurePosixPath(text)
    if not text or relative.is_absolute() or ".." in relative.parts or "\\" in text or "\x00" in text or ":" in text or "$" in text:
        raise ValueError("The command must name a bundled file inside this package.")
    if relative.name in {"npx", "uvx", "pip", "pip3", "npm", "curl", "wget"}:
        raise ValueError("Runtime downloads and package launchers are not allowed.")
    result = (root / text).resolve()
    if not result.is_relative_to(root.resolve()) or result == root.resolve():
        raise ValueError("The command leaves the package.")
    return result


def permission_wall(actions):
    if not isinstance(actions, (list, tuple)) or any(not isinstance(item, str) for item in actions):
        raise ValueError("Permission actions must be a list of names.")
    if any(item in PAID or MONEY.search(re.sub(r"([a-z])([A-Z])", r"\1_\2", item)) for item in actions):
        raise ValueError("Plugins cannot request wallet, payment, signing, approval, cap, budget or receipt authority.")


def _flags(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("Manifest field names must be strings.")
            normalized = re.sub(r"([a-z])([A-Z])", r"\1_\2", key).lower().replace("-", "_")
            if normalized in {"auto_enable", "auto_start", "autostart", "owner_credentials", "owner_keys", "owner_paid_keys", "owner_model_keys"}:
                raise ValueError("Plugins cannot start automatically or request owner credentials.")
            if key.lower() in {"wallet", "payments", "signing", "approval", "caps", "budget", "receipts"}:
                raise ValueError("Plugin packages cannot declare money or approval authority.")
            _flags(item)
    elif isinstance(value, list):
        for item in value:
            _flags(item)


def validate_manifest(raw: dict, *, root: Path) -> dict:
    errors = validate_schema(raw, MANIFEST_SCHEMA)
    if errors:
        raise ValueError("; ".join(errors[:8]))
    _flags(raw)
    permission_wall(raw.get("permissions", []))
    for binding in raw.get("credential_bindings", []):
        if not isinstance(binding, str) or binding.startswith(("llm.cloud.", "wallet.", "signing.", "owner.")):
            raise ValueError("Plugins cannot bind owner model or wallet credentials.")
        if str(binding).startswith("plugin.") and not str(binding).startswith("plugin." + raw["name"] + "."):
            raise ValueError("A plugin cannot reference another plugin's credentials.")
    extension = raw.get("vool", {})
    tools = extension.get("tools", raw.get("tools", [])) or []
    if not isinstance(tools, list) or any(not isinstance(tool, dict) for tool in tools):
        raise ValueError("Plugin tools must be a list of tool declarations.")
    for tool in tools:
        permission_wall(tool.get("permission_actions", []))
        if tool.get("side_effect_class") in PAID or MONEY.search(tool.get("intent", "")):
            raise ValueError("Plugins cannot declare wallet, paid or approval tools.")
        for name in tool.get("handler", {}).get("env_allowlist", []):
            credential_env_name(name)
    if not extension or "id" not in extension:
        return {}
    if extension["id"] != raw["name"] or extension["version"] != raw.get("version"):
        raise ValueError("The plugin id and version must match the package name and version.")
    if len(set(extension["kind"])) != len(extension["kind"]):
        raise ValueError("Plugin kinds must not be duplicated.")
    https_url(extension["homepage"])
    hosts = extension["network"]
    if len(set(hosts)) != len(hosts):
        raise ValueError("Network hosts must not be duplicated.")
    for host in hosts:
        host_name(host)
    credentials = extension["credentials"]
    env = [row["env"] for row in credentials.values()]
    if len(set(env)) != len(env):
        raise ValueError("Each credential needs a separate environment name.")
    for row in credentials.values():
        credential_env_name(row["env"])
        https_url(row["help_url"])
    servers = extension.get("mcpServers", {})
    if servers and "tools" not in extension["kind"]:
        raise ValueError("MCP servers require the tools kind.")
    for server in servers.values():
        package_command(server["command"], root)
        if any(key not in env or value != "${" + key + "}" for key, value in server.get("env", {}).items()):
            raise ValueError("MCP env may name only this plugin's declared credential slots, never values.")
        if any("${" in arg or "\x00" in arg for arg in server.get("args", [])):
            raise ValueError("MCP arguments cannot expand environment variables.")
    permission_wall(raw.get("permissions", []))
    tools = extension.get("tools", raw.get("tools", []))
    if raw.get("tools") is not None and extension.get("tools") is not None and raw["tools"] != extension["tools"]:
        raise ValueError("The package contains conflicting tool declarations.")
    if tools and "tools" not in extension["kind"]:
        raise ValueError("Tool declarations require the tools kind.")
    for tool in tools:
        permission_wall(tool.get("permission_actions", []))
        if tool.get("side_effect_class") in PAID:
            raise ValueError("Plugins cannot declare paid or wallet tools.")
        handler = tool["handler"]
        if handler["kind"] == "subprocess":
            package_command(handler.get("entry", ""), root)
            if set(handler.get("env_allowlist", [])) - set(env):
                raise ValueError("Handlers may receive only this plugin's declared credential environment names.")
            if handler.get("network") and not hosts:
                raise ValueError("A network handler must declare network hosts.")
        else:
            if handler.get("server") not in servers or not handler.get("tool"):
                raise ValueError("An MCP tool must name a bundled server and tool.")
            if MONEY.search(handler["tool"]):
                raise ValueError("Plugins cannot expose MCP money or approval tools.")
            if "trust" in tool:
                permission_wall(tool["trust"]["permission_actions"])
                if tool["trust"]["side_effect_class"] in PAID:
                    raise ValueError("Plugins cannot pin money authority.")
    for kind in ("model_provider", "scanner"):
        block = extension.get(kind)
        if (kind in extension["kind"]) != (block is not None):
            raise ValueError(f"The {kind} declaration must match the plugin kind.")
        if block is None:
            continue
        base = https_url(block["base_url"], hosts)
        if base.query:
            raise ValueError("Service base URLs cannot contain query parameters.")
        if block["credential_slot"] not in credentials:
            raise ValueError("A service may read only its own declared credential slot.")
        for key, value in block.items():
            if key.endswith("_path"):
                if not value.startswith("/") or value.startswith("//") or any(ch in value for ch in ("?", "#", "\\", "\x00")) or ".." in value:
                    raise ValueError("Service paths must be bounded relative paths.")
        if kind == "model_provider":
            if block["auth_placement"] == "path" and block.get("auth_path", "").count("{credential}") != 1:
                raise ValueError("Path authentication needs exactly one credential placeholder.")
            if block["auth_placement"] == "header" and "auth_path" in block:
                raise ValueError("Header authentication cannot also carry a credential path.")
            pricing = block["pricing_source"]
            if pricing != "models":
                https_url(pricing, hosts)
        else:
            json_schema_lite.assert_supported(block["response_schema"])
    return deepcopy(extension)
