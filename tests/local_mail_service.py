"""A real, disposable local SMTP + IMAP mail service for email-path tests.

Not a mock: real TCP sockets, real protocol state machines, real flag storage. The
production `core.email_tools` code under test connects to it exactly the way it
connects to a provider — SMTP (plain / STARTTLS / implicit TLS) and IMAP4rev1
(plain / STARTTLS / implicit TLS) — so a passing test exercised the real wire
path, not a monkeypatched constructor.

Implemented on purpose (what the production client actually sends):
  SMTP: EHLO/HELO, STARTTLS, AUTH PLAIN + AUTH LOGIN, MAIL FROM, RCPT TO, DATA
        (with dot-unstuffing), RSET, NOOP, QUIT.
  IMAP: greeting + CAPABILITY, LOGIN, LIST, SELECT (READ-WRITE) and EXAMINE
        (READ-ONLY), SEARCH (ALL/SEEN/UNSEEN/ANSWERED/FROM/SUBJECT/SINCE/BEFORE/
        HEADER/NOT/OR, including parenthesised lists), FETCH (FLAGS, BODY[],
        BODY.PEEK[], RFC822, ENVELOPE-ish ALL), STATUS, STORE (seen flags),
        NOOP, CLOSE, LOGOUT.

Semantics the acceptance matrix depends on:
  * EXAMINE + BODY.PEEK[] never set \\Seen; SELECT + BODY[] does.
  * Delivered mail lands in the recipient's INBOX; mail an authenticated user
    sends is also filed into that user's "Sent" folder (greenmail-style), which
    is what makes delivery reconciliation provable.
  * `drop_after_accept` closes the connection AFTER a message was accepted and
    stored but BEFORE the 250 reply is written: the client can never know
    whether the server took it. That is the "uncertain SMTP acceptance" case.

Not a pytest module (no test_* functions); it is infrastructure imported by
tests or run standalone:
    python tests/local_mail_service.py --smtp-port 12461 --imap-port 12462 \
        --cert-dir /tmp/mail-certs --state /tmp/mail-state.json
"""
from __future__ import annotations

import base64
import contextlib
import email as email_lib
import email.utils
import json
import socket
import ssl
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_SMTP_PORT = 12461
DEFAULT_IMAP_PORT = 12462


def _date_of(raw: bytes) -> datetime:
    msg = email_lib.message_from_bytes(raw)
    try:
        parsed = email.utils.parsedate_to_datetime(str(msg.get("Date") or ""))
    except (TypeError, ValueError):
        parsed = None
    if parsed is None:
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _decoded_header(msg: Any, name: str) -> str:
    from email.header import decode_header, make_header

    raw = msg.get(name)
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(str(raw))))
    except Exception:
        return str(raw)


@dataclass
class StoredMessage:
    raw: bytes
    flags: set[str] = field(default_factory=set)
    internal_date: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def msg(self) -> Any:
        return email_lib.message_from_bytes(self.raw)

    @property
    def message_id(self) -> str:
        return str(self.msg.get("Message-ID") or "").strip()

    def matches(self, tokens: list[tuple[str, str]]) -> bool:
        msg = self.msg
        for kind, value in tokens:
            if kind == "ALL":
                continue
            if kind == "SEEN":
                if "\\Seen" not in self.flags:
                    return False
            elif kind == "UNSEEN":
                if "\\Seen" in self.flags:
                    return False
            elif kind == "ANSWERED":
                if "\\Answered" not in self.flags:
                    return False
            elif kind == "FROM":
                if value.lower() not in _decoded_header(msg, "From").lower():
                    return False
            elif kind == "TO":
                if value.lower() not in _decoded_header(msg, "To").lower():
                    return False
            elif kind == "SUBJECT":
                if value.lower() not in _decoded_header(msg, "Subject").lower():
                    return False
            elif kind == "BODY":
                if value.lower() not in self.raw.lower():
                    return False
            elif kind == "SINCE":
                if self.internal_date < _parse_imap_date(value):
                    return False
            elif kind == "BEFORE":
                if self.internal_date >= _parse_imap_date(value):
                    return False
            elif kind == "ON":
                if self.internal_date.date() != _parse_imap_date(value).date():
                    return False
            elif kind == "HEADER":
                field_name, _, field_value = value.partition(" ")
                header_value = _decoded_header(msg, field_name.strip())
                if field_value.strip().lower() not in header_value.lower():
                    return False
            elif kind == "MESSAGE-ID":
                if value.lower() not in self.message_id.lower():
                    return False
        return True


def _parse_imap_date(value: str) -> datetime:
    for fmt in ("%d-%b-%Y", "%d-%b-%y"):
        try:
            return datetime.strptime(value.strip().strip('"'), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return datetime.now(timezone.utc)


class MailStore:
    """Users, folders, messages — the durable side of the service."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.users: dict[str, dict[str, Any]] = {}
        # Every accepted SMTP submission, in order, with full bytes + envelope.
        self.captured: list[dict[str, Any]] = []

    # -- accounts ---------------------------------------------------------
    def add_user(self, address: str, password: str, folders: dict[str, list[bytes]] | None = None) -> None:
        with self._lock:
            boxes: dict[str, list[StoredMessage]] = {"INBOX": [], "Sent": []}
            for folder, raws in (folders or {}).items():
                boxes.setdefault(folder, [])
                for raw in raws:
                    boxes[folder].append(
                        StoredMessage(raw=raw, flags=set(), internal_date=_date_of(raw))
                    )
            self.users[address] = {"password": password, "folders": boxes}

    # -- delivery ---------------------------------------------------------
    def deliver(self, recipient: str, raw: bytes, seen: bool = False) -> bool:
        with self._lock:
            user = self.users.get(recipient)
            if user is None:
                return False
            entry = StoredMessage(raw=raw, flags={"\\Seen"} if seen else set(),
                                  internal_date=_date_of(raw) or datetime.now(timezone.utc))
            user["folders"].setdefault("INBOX", []).append(entry)
            return True

    def file_sent(self, sender: str, raw: bytes) -> None:
        with self._lock:
            user = self.users.get(sender)
            if user is not None:
                user["folders"].setdefault("Sent", []).append(
                    StoredMessage(raw=raw, flags={"\\Seen"}, internal_date=_date_of(raw))
                )

    # -- inspection (test assertions) --------------------------------------
    def folder(self, address: str, folder: str = "INBOX") -> list[StoredMessage]:
        with self._lock:
            user = self.users.get(address)
            if user is None:
                return []
            return list(user["folders"].get(folder, []))

    def seen_flags(self, address: str, folder: str = "INBOX") -> list[bool]:
        return ["\\Seen" in m.flags for m in self.folder(address, folder)]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._state()))  # deep, detached copy

    def _state(self) -> dict[str, Any]:
        return {
            "users": {
                addr: {
                    "folders": {
                        name: [
                            {
                                "message_id": m.message_id,
                                "subject": _decoded_header(m.msg, "Subject"),
                                "from": _decoded_header(m.msg, "From"),
                                "to": _decoded_header(m.msg, "To"),
                                "in_reply_to": str(m.msg.get("In-Reply-To") or ""),
                                "references": str(m.msg.get("References") or ""),
                                "seen": "\\Seen" in m.flags,
                            }
                            for m in msgs
                        ]
                        for name, msgs in user["folders"].items()
                    }
                }
                for addr, user in self.users.items()
            },
            "captured": [
                {
                    "from": c["from"],
                    "recipients": c["recipients"],
                    "message_id": c["message_id"],
                    "raw_bytes": len(c["raw"]),
                }
                for c in self.captured
            ],
        }


def _make_cert(cert_dir: Path, common_name: str = "localhost") -> tuple[Path, Path]:
    """A real, throwaway CA-signed pair for implicit-TLS / STARTTLS listeners."""
    import datetime as dt

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    cert_dir.mkdir(parents=True, exist_ok=True)
    key_path = cert_dir / "server-key.pem"
    cert_path = cert_dir / "server-cert.pem"
    if key_path.exists() and cert_path.exists():
        return cert_path, key_path
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(hours=1))
        .not_valid_after(now + dt.timedelta(days=2))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(common_name)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


class LocalMailService:
    """SMTP + IMAP on loopback. One instance serves both protocols."""

    def __init__(
        self,
        *,
        smtp_port: int = DEFAULT_SMTP_PORT,
        imap_port: int = DEFAULT_IMAP_PORT,
        cert_dir: Path | None = None,
        host: str = "127.0.0.1",
        smtp_tls_port: int | None = None,
        imap_tls_port: int | None = None,
    ) -> None:
        self.host = host
        self.smtp_port = smtp_port
        self.imap_port = imap_port
        self.smtp_tls_port = smtp_tls_port
        self.imap_tls_port = imap_tls_port
        self.store = MailStore()
        self.drop_after_accept = False  # chaos: lose the connection post-acceptance
        self._cert_dir = cert_dir
        self._ssl_context: ssl.SSLContext | None = None
        self._servers: list[socket.socket] = []
        self._threads: list[threading.Thread] = []
        self._stopping = threading.Event()

    # -- lifecycle ---------------------------------------------------------
    def __enter__(self) -> LocalMailService:
        self.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.stop()

    def start(self) -> None:
        """Bind every listener. A port of 0 means EPHEMERAL: the OS picks a free
        port and the instance records the actual value (`smtp_port`, `imap_port`,
        `smtp_tls_port`, `imap_tls_port`), so callers wire credentials to what was
        actually bound. This is how the tests avoid ever occupying another
        mission's allocated ports."""
        if self._cert_dir is not None:
            cert_path, key_path = _make_cert(self._cert_dir)
            self._ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            self._ssl_context.load_cert_chain(str(cert_path), str(key_path))
        # (requested port, handler, implicit-TLS, attribute to record the ACTUAL port on)
        listeners: list[tuple[int, Any, bool, str]] = [
            (self.smtp_port, self._smtp_session, False, "smtp_port"),
            (self.imap_port, self._imap_session, False, "imap_port"),
        ]
        # TLS listeners: None disables the listener; 0 requests an ephemeral port.
        if self._ssl_context is not None and self.smtp_tls_port is not None:
            listeners.append((self.smtp_tls_port, self._smtp_session, True, "smtp_tls_port"))
        if self._ssl_context is not None and self.imap_tls_port is not None:
            listeners.append((self.imap_tls_port, self._imap_session, True, "imap_tls_port"))
        for port, handler, implicit_tls, attr in listeners:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((self.host, port))
            actual_port = server.getsockname()[1]
            server.listen(16)
            server.settimeout(0.25)
            self._servers.append(server)
            setattr(self, attr, actual_port)
            thread = threading.Thread(
                target=self._accept_loop, args=(server, handler, implicit_tls), daemon=True
            )
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        self._stopping.set()
        for server in self._servers:
            with contextlib.suppress(OSError):
                server.shutdown(socket.SHUT_RDWR)
            with contextlib.suppress(OSError):
                server.close()
        for thread in self._threads:
            thread.join(timeout=2.0)

    def _accept_loop(self, server: socket.socket, handler, implicit_tls: bool = False) -> None:
        while not self._stopping.is_set():
            try:
                conn, _addr = server.accept()
            except (TimeoutError, OSError):
                continue
            if implicit_tls and self._ssl_context is not None:
                try:
                    conn = self._ssl_context.wrap_socket(conn, server_side=True)
                except (OSError, ssl.SSLError):
                    continue
            threading.Thread(target=handler, args=(conn, implicit_tls), daemon=True).start()

    # -- shared IO helpers ---------------------------------------------------
    class _LineReader:
        """One CRLF line at a time from a socket, buffered across recvs.

        A single recv can carry many protocol lines; returning the whole chunk
        as "a line" breaks terminator matching, so reads buffer and split.

        Not a double of anything — this is plain TCP framing.
        """

        def __init__(self, conn: socket.socket) -> None:
            self._conn = conn
            self._buf = bytearray()

        def read_line(self) -> bytes:
            while True:
                idx = self._buf.find(b"\r\n")
                if idx >= 0:
                    line = bytes(self._buf[:idx])
                    del self._buf[: idx + 2]
                    return line
                if len(self._buf) > 8_388_608:
                    raise ConnectionError("line too long")
                chunk = self._conn.recv(65536)
                if not chunk:
                    raise ConnectionError("client closed")
                self._buf += chunk

    @staticmethod
    def _send(conn: socket.socket, line: str) -> None:
        conn.sendall((line + "\r\n").encode("utf-8"))

    # -- SMTP ----------------------------------------------------------------
    def _smtp_session(self, conn: socket.socket, tls_on: bool = False) -> None:
        try:
            conn.settimeout(30)
            reader = self._LineReader(conn)
            self._send(conn, "220 local-mail-service ESMTP ready")
            mail_from: str | None = None
            recipients: list[str] = []
            while True:
                line = reader.read_line().decode("utf-8", "replace")
                verb = line.split(" ", 1)[0].upper() if line else ""
                if verb in {"EHLO", "HELO"}:
                    if self._ssl_context is not None and not tls_on:
                        self._send(conn, "250-local-mail-service")
                        self._send(conn, "250-STARTTLS")
                        self._send(conn, "250-AUTH LOGIN PLAIN")
                        self._send(conn, "250 SIZE 26214400")
                    else:
                        self._send(conn, "250-local-mail-service")
                        self._send(conn, "250-AUTH LOGIN PLAIN")
                        self._send(conn, "250 SIZE 26214400")
                elif verb == "STARTTLS":
                    if self._ssl_context is None or tls_on:
                        self._send(conn, "454 TLS not available")
                        continue
                    self._send(conn, "220 Ready to start TLS")
                    conn = self._ssl_context.wrap_socket(conn, server_side=True)
                    reader = self._LineReader(conn)
                    tls_on = True
                elif verb == "AUTH":
                    if self._ssl_context is not None and not tls_on:
                        # Channel-order law: when this service offers STARTTLS,
                        # credentials are refused on the cleartext channel. This is
                        # the wire-level control the revision tests drive a raw
                        # client against (no AUTH before the secure channel).
                        self._send(conn, "538 5.7.11 Encryption required for requested authentication mechanism")
                        continue
                    parts = line.split(" ", 2)
                    method = parts[1].upper() if len(parts) > 1 else ""
                    if method == "PLAIN":
                        payload = parts[2] if len(parts) > 2 else None
                        if payload is None:
                            self._send(conn, "334 ")
                            payload = reader.read_line().decode("utf-8", "replace")
                        decoded = base64.b64decode(payload.strip() or "").split(b"\0")
                        user = decoded[1].decode() if len(decoded) > 1 else ""
                        password = decoded[2].decode() if len(decoded) > 2 else ""
                        self._auth(conn, user, password)
                    elif method == "LOGIN":
                        self._send(conn, "334 " + base64.b64encode(b"Username:").decode())
                        user = base64.b64decode(reader.read_line().strip()).decode("utf-8", "replace")
                        self._send(conn, "334 " + base64.b64encode(b"Password:").decode())
                        password = base64.b64decode(reader.read_line().strip()).decode("utf-8", "replace")
                        self._auth(conn, user, password)
                    else:
                        self._send(conn, "504 Unrecognized authentication type")
                elif verb == "MAIL":
                    mail_from = _angle_address(line)
                    recipients = []
                    self._send(conn, "250 OK")
                elif verb == "RCPT":
                    recipients.append(_angle_address(line))
                    self._send(conn, "250 OK")
                elif verb == "DATA":
                    if mail_from is None:
                        self._send(conn, "503 MAIL first")
                        continue
                    self._send(conn, "354 End data with <CR><LF>.<CR><LF>")
                    raw = bytearray()
                    while True:
                        data_line = reader.read_line()
                        if data_line == b".":
                            break
                        if data_line.startswith(b".."):
                            data_line = data_line[1:]  # dot-unstuffing
                        raw += data_line + b"\r\n"
                    stored = self._accept_submission(mail_from, recipients, bytes(raw))
                    if self.drop_after_accept:
                        # Accepted and stored, but the 250 is never sent: the
                        # client cannot know whether the server took it.
                        self.drop_after_accept = False
                        with contextlib.suppress(OSError):
                            conn.close()
                        return
                    self._send(conn, "250 OK queued" + (" as " + stored if stored else ""))
                    mail_from, recipients = None, []
                elif verb == "RSET":
                    mail_from, recipients = None, []
                    self._send(conn, "250 OK")
                elif verb == "NOOP":
                    self._send(conn, "250 OK")
                elif verb == "QUIT":
                    self._send(conn, "221 Bye")
                    return
                else:
                    self._send(conn, "500 Command not recognized")
        except (ConnectionError, OSError, ValueError):
            pass
        finally:
            with contextlib.suppress(OSError):
                conn.close()

    def _auth(self, conn: socket.socket, user: str, password: str) -> str | None:
        record = self.store.users.get(user)
        if record is not None and record["password"] == password:
            self._send(conn, "235 authenticated")
            return user
        self._send(conn, "535 authentication failed")
        return None

    def _accept_submission(self, sender: str, recipients: list[str], raw: bytes) -> str:
        msg = email_lib.message_from_bytes(raw)
        message_id = str(msg.get("Message-ID") or "").strip()
        with self.store._lock:
            self.store.captured.append(
                {"from": sender, "recipients": list(recipients), "raw": raw, "message_id": message_id,
                 "at": datetime.now(timezone.utc).isoformat()}
            )
        for recipient in recipients:
            self.store.deliver(recipient, raw, seen=False)
        if sender:
            self.store.file_sent(sender, raw)
        return message_id

    # -- IMAP -----------------------------------------------------------------
    def _imap_session(self, conn: socket.socket, tls_on: bool = False) -> None:
        state: dict[str, Any] = {"authed": None, "folder": None, "readonly": False}
        try:
            conn.settimeout(30)
            capabilities = "IMAP4rev1"
            if self._ssl_context is not None:
                capabilities += " STARTTLS AUTH=PLAIN"
            else:
                capabilities += " AUTH=PLAIN"
            self._send(conn, f"* OK [CAPABILITY {capabilities}] local-mail-service ready")
            reader = self._LineReader(conn)
            while True:
                line = reader.read_line().decode("utf-8", "replace")
                parts = line.split(" ", 2)
                tag = parts[0] if parts else "*"
                command = (parts[1] if len(parts) > 1 else "").upper()
                args = parts[2] if len(parts) > 2 else ""
                if command == "STARTTLS":
                    if self._ssl_context is None or tls_on:
                        self._send(conn, f"{tag} NO STARTTLS not available")
                        continue
                    self._send(conn, f"{tag} OK Begin TLS negotiation now")
                    conn = self._ssl_context.wrap_socket(conn, server_side=True)
                    reader = self._LineReader(conn)
                    tls_on = True
                    continue
                if command == "CAPABILITY":
                    self._send(conn, f"* CAPABILITY {capabilities}")
                    self._send(conn, f"{tag} OK CAPABILITY completed")
                elif command == "NOOP":
                    self._send(conn, f"{tag} OK NOOP completed")
                elif command == "LOGIN":
                    bits = args.split(" ")
                    user = (bits[0] if bits else "").strip().strip('"')
                    password = (bits[1] if len(bits) > 1 else "").strip().strip('"')
                    record = self.store.users.get(user)
                    if record is not None and record["password"] == password:
                        state["authed"] = user
                        self._send(conn, f"{tag} OK [CAPABILITY {capabilities}] LOGIN completed")
                    else:
                        self._send(conn, f"{tag} NO [AUTHENTICATIONFAILED] invalid credentials")
                elif command == "AUTHENTICATE":
                    self._send(conn, f"{tag} NO use LOGIN")
                elif command == "LIST":
                    self._send(conn, '* LIST (\\HasNoChildren) "/" "INBOX"')
                    self._send(conn, '* LIST (\\HasNoChildren) "/" "Sent"')
                    self._send(conn, f"{tag} OK LIST completed")
                elif command in {"SELECT", "EXAMINE"}:
                    folder = args.strip().strip('"')
                    user = self.store.users.get(state["authed"] or "")
                    if user is None or folder not in user["folders"]:
                        self._send(conn, f"{tag} NO no such mailbox")
                        continue
                    state["folder"] = folder
                    state["readonly"] = command == "EXAMINE"
                    messages = user["folders"][folder]
                    unseen = [i + 1 for i, m in enumerate(messages) if "\\Seen" not in m.flags]
                    self._send(conn, f"* {len(messages)} EXISTS")
                    self._send(conn, "* 0 RECENT")
                    self._send(conn, f"* OK [UNSEEN {unseen[0]}]" if unseen else "* OK [UNSEEN 0]")
                    self._send(conn, "* OK [UIDVALIDITY 1]")
                    self._send(conn, f"* OK [UIDNEXT {len(messages) + 1}]")
                    self._send(conn, "* FLAGS (\\Answered \\Flagged \\Deleted \\Seen \\Draft)")
                    self._send(
                        conn,
                        f"{tag} OK [READ-{'ONLY' if state['readonly'] else 'WRITE'}] {command} completed",
                    )
                elif command == "STATUS":
                    folder = args.split(" ", 1)[0].strip().strip('"')
                    user = self.store.users.get(state["authed"] or "")
                    if user is None or folder not in user["folders"]:
                        self._send(conn, f"{tag} NO no such mailbox")
                        continue
                    messages = user["folders"][folder]
                    unseen = sum(1 for m in messages if "\\Seen" not in m.flags)
                    self._send(conn, f"* STATUS {folder} (MESSAGES {len(messages)} UNSEEN {unseen})")
                    self._send(conn, f"{tag} OK STATUS completed")
                elif command == "SEARCH":
                    if not state.get("folder"):
                        self._send(conn, f"{tag} NO no mailbox selected")
                        continue
                    tokens = _tokenize_search(args)
                    user = self.store.users.get(state["authed"] or "")
                    messages = user["folders"][state["folder"]]
                    hits = [str(i + 1) for i, m in enumerate(messages) if m.matches(tokens)]
                    self._send(conn, "* SEARCH" + (" " + " ".join(hits) if hits else ""))
                    self._send(conn, f"{tag} OK SEARCH completed")
                elif command == "FETCH":
                    if not state.get("folder"):
                        self._send(conn, f"{tag} NO no mailbox selected")
                        continue
                    spec_range, _, spec = args.partition(" ")
                    user = self.store.users.get(state["authed"] or "")
                    messages = user["folders"][state["folder"]]
                    for seq in _expand_sequence(spec_range, len(messages)):
                        entry = messages[seq - 1]
                        self._fetch_one(conn, seq, entry, spec, state)
                    self._send(conn, f"{tag} OK FETCH completed")
                elif command == "STORE":
                    if not state.get("folder"):
                        self._send(conn, f"{tag} NO no mailbox selected")
                        continue
                    if state["readonly"]:
                        self._send(conn, f"{tag} NO mailbox is read-only")
                        continue
                    bits = args.split(" ", 2)
                    user = self.store.users.get(state["authed"] or "")
                    messages = user["folders"][state["folder"]]
                    flag_value = bits[2].strip() if len(bits) > 2 else ""
                    add = flag_value.startswith("+")
                    remove = flag_value.startswith("-")
                    flag = flag_value.strip("+-FLAGS() ").strip()
                    for seq in _expand_sequence(bits[0], len(messages)):
                        entry = messages[seq - 1]
                        if add:
                            entry.flags.add(flag)
                        elif remove:
                            entry.flags.discard(flag)
                        self._send(conn, f"* {seq} FETCH (FLAGS ({' '.join(sorted(entry.flags)) or ''}))")
                    self._send(conn, f"{tag} OK STORE completed")
                elif command == "CLOSE":
                    state["folder"] = None
                    self._send(conn, f"{tag} OK CLOSE completed")
                elif command == "LOGOUT":
                    self._send(conn, "* BYE local-mail-service logging out")
                    self._send(conn, f"{tag} OK LOGOUT completed")
                    return
                else:
                    self._send(conn, f"{tag} BAD command not recognized")
        except (ConnectionError, OSError, ValueError):
            pass
        finally:
            with contextlib.suppress(OSError):
                conn.close()

    def _fetch_one(self, conn: socket.socket, seq: int, entry: StoredMessage, spec: str, state: dict[str, Any]) -> None:
        import re as _re

        spec = spec.strip()
        upper = spec.upper()
        peek = "PEEK" in upper
        plain_body = "BODY[]" in upper.replace("BODY.PEEK[]", "BODY[]")
        header_fields = _re.search(r"HEADER\.FIELDS\s*\(([^)]*)\)", upper)
        parts: list[str] = []
        if "FLAGS" in upper or "ALL" in upper or "FAST" in upper:
            parts.append(f"FLAGS ({' '.join(sorted(entry.flags)) or ''})")
        if "INTERNALDATE" in upper or "ALL" in upper:
            parts.append(f'INTERNALDATE "{entry.internal_date.strftime("%d-%b-%Y %H:%M:%S +0000")}"')
        if "RFC822.SIZE" in upper or "ALL" in upper:
            parts.append(f"RFC822.SIZE {len(entry.raw)}")
        response = f"* {seq} FETCH (" + " ".join(parts)
        if plain_body:
            if not peek and not state["readonly"]:
                entry.flags.add("\\Seen")
            conn.sendall((response + ("" if response.endswith("(") else " ") + "BODY[]").encode("utf-8") + b" ")
            conn.sendall(b"{%d}\r\n" % len(entry.raw))
            conn.sendall(entry.raw)
            conn.sendall(b")\r\n")
            if not peek and not state["readonly"]:
                self._send(conn, f"* {seq} FETCH (FLAGS ({' '.join(sorted(entry.flags)) or ''}))")
            return
        if header_fields:
            wanted = [name.strip().lower() for name in header_fields.group(1).split()]
            msg = email_lib.message_from_bytes(entry.raw)
            subset = bytearray()
            for name in msg:
                if name.lower() in wanted:
                    subset += f"{name}: {msg.get(name)}\r\n".encode("utf-8", "replace")
            subset += b"\r\n"
            token = "BODY[HEADER.FIELDS (" + " ".join(w.upper() for w in wanted) + ")]"
            conn.sendall((response + ("" if response.endswith("(") else " ") + token).encode("utf-8") + b" ")
            conn.sendall(b"{%d}\r\n" % len(subset))
            conn.sendall(bytes(subset))
            conn.sendall(b")\r\n")
            return
        if not parts:
            # Unknown spec: return flags so strict clients still parse a fetch.
            response = f"* {seq} FETCH (FLAGS ({' '.join(sorted(entry.flags)) or ''}))"
        else:
            response += ")"
        self._send(conn, response)


def _angle_address(line: str) -> str:
    """The address inside <...>, ignoring trailing ESMTP options like `size=207`."""
    import re as _re

    match = _re.search(r"<([^>]*)>", line)
    if match:
        return match.group(1).strip()
    # Bare `MAIL FROM:addr` without brackets.
    value = line.split(":", 1)[1].strip() if ":" in line else ""
    return value.split(" ")[0].strip()


def _expand_sequence(spec: str, total: int) -> list[int]:
    spec = spec.strip()
    if spec == "*":
        return [total] if total else []
    if ":" in spec:
        start_s, _, end_s = spec.partition(":")
        start = total if start_s == "*" else int(start_s)
        end = total if end_s == "*" else int(end_s)
        lo, hi = min(start, end), max(start, end)
        return [i for i in range(lo, hi + 1) if 1 <= i <= total]
    if spec.isdigit():
        value = int(spec)
        return [value] if 1 <= value <= total else []
    if "," in spec:
        out: list[int] = []
        for piece in spec.split(","):
            out.extend(_expand_sequence(piece, total))
        return out
    return []


def _tokenize_search(args: str) -> list[tuple[str, str]]:
    """Split an IMAP SEARCH argument string into flat AND-ed (key, value) tokens.

    Supports the criteria the production client builds — ALL / SEEN / UNSEEN /
    ANSWERED / FROM / TO / SUBJECT / BODY / SINCE / BEFORE / ON / HEADER and
    parenthesised groups of those — over quoted strings and bare atoms. NOT and
    OR are deliberately not implemented: the client never emits them, and the
    client-side refinement in `core.email_tools` covers anything richer.
    """
    tokens: list[tuple[str, str]] = []
    value_keys = {"FROM", "TO", "SUBJECT", "BODY", "SINCE", "BEFORE", "ON", "HEADER"}
    i = 0
    text = args.strip()
    while i < len(text):
        ch = text[i]
        if ch == " ":
            i += 1
            continue
        if ch == "(":
            depth, j = 1, i + 1
            while j < len(text) and depth:
                if text[j] == "(":
                    depth += 1
                elif text[j] == ")":
                    depth -= 1
                j += 1
            tokens.extend(_tokenize_search(text[i + 1 : j - 1]))
            i = j
            continue
        if ch == '"':
            close = text.find('"', i + 1)
            close = len(text) if close == -1 else close
            tokens.append(("VALUE", text[i + 1 : close]))
            i = close + 1
            continue
        j = i
        while j < len(text) and text[j] not in ' ("':
            j += 1
        word = text[i:j]
        upper = word.upper()
        if upper in value_keys:
            k = j
            while k < len(text) and text[k] == " ":
                k += 1
            if k >= len(text):
                tokens.append((upper, ""))
                break
            if text[k] == '"':
                close = text.find('"', k + 1)
                close = len(text) if close == -1 else close
                value = text[k + 1 : close]
                i = close + 1
            else:
                end = k
                while end < len(text) and text[end] != " ":
                    end += 1
                value = text[k:end]
                i = end
            if upper == "HEADER":
                # HEADER <field-name> <value>: two values, joined by one space.
                k2 = i
                while k2 < len(text) and text[k2] == " ":
                    k2 += 1
                if k2 < len(text):
                    if text[k2] == '"':
                        close2 = text.find('"', k2 + 1)
                        close2 = len(text) if close2 == -1 else close2
                        second = text[k2 + 1 : close2]
                        i = close2 + 1
                    else:
                        end2 = k2
                        while end2 < len(text) and text[end2] != " ":
                            end2 += 1
                        second = text[k2:end2]
                        i = end2
                    value = f"{value} {second}"
            tokens.append((upper, value))
            continue
        tokens.append((upper, word))
        i = j
    return tokens


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smtp-port", type=int, default=DEFAULT_SMTP_PORT)
    parser.add_argument("--imap-port", type=int, default=DEFAULT_IMAP_PORT)
    parser.add_argument("--cert-dir", type=Path, default=None)
    parser.add_argument("--state", type=Path, default=Path("/tmp/local-mail-state.json"))
    parser.add_argument("--seed", type=Path, default=None, help="JSON {user: {password, folders: {INBOX: [raw base64]}}}")
    args = parser.parse_args(argv)

    service = LocalMailService(smtp_port=args.smtp_port, imap_port=args.imap_port, cert_dir=args.cert_dir)
    if args.seed and args.seed.exists():
        seed = json.loads(args.seed.read_text())
        for address, spec in seed.items():
            folders = {
                name: [base64.b64decode(raw) for raw in raws]
                for name, raws in (spec.get("folders") or {}).items()
            }
            service.store.add_user(address, spec.get("password", ""), folders)
    service.start()
    print(f"SMTP on {args.smtp_port}, IMAP on {args.imap_port}; state -> {args.state}", flush=True)
    try:
        while True:
            time.sleep(5)
            args.state.write_text(json.dumps(service.store.snapshot(), indent=2), encoding="utf-8")
    except KeyboardInterrupt:
        service.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
