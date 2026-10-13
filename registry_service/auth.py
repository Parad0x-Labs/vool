"""Provider authorization-code login with minimal stable identities and no token storage."""
from __future__ import annotations

import base64
import hashlib
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from core.registry.codec import RegistryError, decode, require
from registry_service.store import Store

PROVIDERS = {
    "github": {"authorize": "https://github.com/login/oauth/authorize",
               "exchange": "https://github.com/login/oauth/access_token"},
    "google": {"authorize": "https://accounts.google.com/o/oauth2/v2/auth",
               "exchange": "https://oauth2.googleapis.com/token",
               "keys": "https://www.googleapis.com/oauth2/v3/certs",
               "issuers": {"https://accounts.google.com", "accounts.google.com"}},
    "apple": {"authorize": "https://appleid.apple.com/auth/authorize",
              "exchange": "https://appleid.apple.com/auth/token",
              "keys": "https://appleid.apple.com/auth/keys", "issuers": {"https://appleid.apple.com"}},
}


@dataclass(frozen=True)
class Provider:
    name: str
    client_id: str
    client_secret: str


def url64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def unurl64(value: str) -> bytes:
    require(isinstance(value, str) and len(value) <= 16384 and bool(re.fullmatch(r"[A-Za-z0-9_-]+", value)),
            "invalid JWT encoding")
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except ValueError:
        raise RegistryError("invalid JWT encoding") from None


def oidc_subject(token: str, jwks: dict, provider: Provider, nonce: str, *, now: int) -> str:
    require(isinstance(token, str) and len(token) <= 16384 and token.count(".") == 2, "invalid identity token")
    header_part, payload_part, signature_part = token.split(".")
    header, payload = decode(unurl64(header_part), 4096), decode(unurl64(payload_part), 12000)
    require(isinstance(header, dict) and set(header) <= {"alg", "kid", "typ"} and header.get("alg") == "RS256" and
            isinstance(header.get("kid"), str), "unsupported identity signature")
    require(isinstance(jwks, dict) and isinstance(jwks.get("keys"), list) and len(jwks["keys"]) <= 32,
            "invalid provider key set")
    matching = [key for key in jwks["keys"] if isinstance(key, dict) and key.get("kid") == header["kid"]]
    require(len(matching) == 1, "identity signing key unknown or ambiguous")
    key = matching[0]
    require(key.get("kty") == "RSA" and key.get("use", "sig") == "sig" and key.get("alg", "RS256") == "RS256",
            "invalid provider signing key")
    n, e = int.from_bytes(unurl64(key.get("n")), "big"), int.from_bytes(unurl64(key.get("e")), "big")
    require(2048 <= n.bit_length() <= 8192 and 3 <= e <= 16777217, "unsafe provider key")
    try:
        rsa.RSAPublicNumbers(e, n).public_key().verify(unurl64(signature_part),
            (header_part + "." + payload_part).encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    except (InvalidSignature, ValueError):
        raise RegistryError("invalid identity signature") from None
    require(isinstance(payload, dict) and payload.get("iss") in PROVIDERS[provider.name]["issuers"], "identity issuer mismatch")
    aud = payload.get("aud")
    require(aud == provider.client_id or (isinstance(aud, list) and provider.client_id in aud and
                                        payload.get("azp") == provider.client_id), "identity audience mismatch")
    require(payload.get("nonce") == nonce and type(payload.get("exp")) is int and type(payload.get("iat")) is int and
            payload["iat"] <= now < payload["exp"] <= now + 86400 and
            ("nbf" not in payload or (type(payload["nbf"]) is int and payload["nbf"] <= now)), "expired or replayed identity token")
    subject = payload.get("sub")
    require(isinstance(subject, str) and 0 < len(subject) <= 255, "missing stable provider identity")
    return subject


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RegistryError("provider redirect refused")


def provider_request(url: str, data: dict | None = None, *, token: str = "") -> dict:
    allowed = {item[key] for item in PROVIDERS.values() for key in ("exchange", "keys") if key in item}
    allowed.add("https://api.github.com/user")
    require(url in allowed, "provider endpoint is not pinned")
    headers = {"Accept": "application/json", "User-Agent": "VOOL-registry"}
    if token:
        require(len(token) <= 4096 and "\r" not in token and "\n" not in token, "invalid provider access token")
        headers["Authorization"] = "Bearer " + token
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    if body is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=15) as response:
            result = decode(response.read(65537), 65536)
            require(isinstance(result, dict), "invalid provider response")
            return result
    except (urllib.error.URLError, OSError):
        raise RegistryError("identity provider unavailable") from None


class Auth:
    def __init__(self, store: Store, origin: str, providers: list[Provider], *, request=provider_request):
        parsed = urllib.parse.urlsplit(origin)
        require(parsed.scheme == "https" and bool(parsed.netloc) and not parsed.path and
                not parsed.query and not parsed.fragment and not parsed.username, "HTTPS origin required")
        require(all(provider.name in PROVIDERS and provider.client_id and provider.client_secret for provider in providers),
                "provider registration is incomplete")
        self.store, self.origin, self.request = store, origin, request
        self.providers = {provider.name: provider for provider in providers}
        require(len(self.providers) == len(providers), "duplicate provider registration")

    def begin(self, name: str, *, now: int, link_account: str = "", browser: str = "") -> tuple[str, str]:
        require(name in self.providers, "login provider is not configured")
        provider, endpoints = self.providers[name], PROVIDERS[name]
        require(isinstance(browser, str) and (not browser or bool(re.fullmatch(r"[A-Za-z0-9_-]{32,128}", browser))),
                "invalid login browser binding")
        browser = browser or secrets.token_urlsafe(32)
        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        state_hash = hashlib.sha256(state.encode()).hexdigest()
        browser_hash = hashlib.sha256(browser.encode()).hexdigest()
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM oauth WHERE expires<=?", (now,))
            require(db.execute("SELECT count(*) FROM oauth WHERE browser=? AND provider=?", (browser_hash, name)).fetchone()[0] < 8,
                    "pending login capacity reached for this browser and provider")
            db.execute("INSERT INTO oauth VALUES (?,?,?,?,?,?,?)", (state_hash, name, verifier, nonce, browser_hash, now + 600, link_account))
        params = {"client_id": provider.client_id, "redirect_uri": self.origin + "/registry/api/auth/" + name + "/callback",
                  "response_type": "code", "state": state}
        if name == "apple":
            params.update(response_mode="form_post", nonce=nonce)
        else:
            params.update(code_challenge=url64(hashlib.sha256(verifier.encode()).digest()), code_challenge_method="S256")
            if name == "google":
                params.update(scope="openid", nonce=nonce)
        return endpoints["authorize"] + "?" + urllib.parse.urlencode(params), browser

    def finish(self, name: str, state: str, code: str, browser: str, *, now: int) -> tuple[str, str]:
        require(name in self.providers and all(isinstance(item, str) and 0 < len(item) <= 4096 for item in (state, code, browser)),
                "invalid login callback")
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM oauth WHERE hash=?", (hashlib.sha256(state.encode()).hexdigest(),)).fetchone()
            require(row and row["provider"] == name and row["expires"] > now and
                    secrets.compare_digest(row["browser"], hashlib.sha256(browser.encode()).hexdigest()), "login state expired or mismatched")
            db.execute("DELETE FROM oauth WHERE hash=?", (row["hash"],))
        provider, endpoints = self.providers[name], PROVIDERS[name]
        payload = {"grant_type": "authorization_code", "code": code, "client_id": provider.client_id,
                   "client_secret": provider.client_secret,
                   "redirect_uri": self.origin + "/registry/api/auth/" + name + "/callback"}
        if name != "apple":
            payload["code_verifier"] = row["verifier"]
        tokens = self.request(endpoints["exchange"], payload)
        require("error" not in tokens, "provider refused authorization")
        if name == "github":
            require(isinstance(tokens.get("access_token"), str) and tokens.get("scope", "") in {"", "read:user"},
                    "GitHub login requested excessive permissions")
            user = self.request("https://api.github.com/user", token=tokens["access_token"])
            require(type(user.get("id")) is int and user["id"] > 0, "GitHub identity unavailable")
            subject = str(user["id"])
        else:
            subject = oidc_subject(tokens.get("id_token"), self.request(endpoints["keys"]), provider, row["nonce"], now=now)
        account = self.store.identity(name, subject, now=now, link_account=row["link_account"])
        return self.store.session(account, now=now)
