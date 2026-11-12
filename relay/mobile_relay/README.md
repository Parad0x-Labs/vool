# Private companion relay

The PC dials out to an explicitly configured relay. Pair on the home network first, then enter the relay connection in **Settings → Phone and chat apps → Use my relay**. The PC token is stored through the existing credential-store API. The relay is disabled by default. Turning phone access off stops the worker. Relay mode closes the inbound companion listener; the desktop API remains loopback-only.

A relay administrator supplies an unpredictable 48–64 character hexadecimal room and a separate PC token (32–128 URL-safe characters). Its owner-only JSON config maps the room to that token. Run `python -m relay.mobile_relay.server --config <file> --port <port>`. It binds loopback: hosting and a properly configured HTTPS reverse proxy belong to the administrator. Do not put its config in version control. The hosted endpoint requires HTTPS; loopback HTTP is accepted for local tests only. Do not log room paths or request bodies in the proxy.

The phone uses `https://<relay>/rooms/<room>` as its connection address, entered in Status. Changing this route preserves the pinned desktop key. A different desktop cannot decrypt the request or produce a valid reply. Pairing and owner APIs are never served by the relay. There is no plaintext fallback. A recovered computer needs new pairing.

Only `api/mobile/companion` packets with the sealed v2 envelope shape are queued. Queue capacity and concurrent connections are bounded. Mailboxes live only in memory; packets time out when the PC is absent. The PC authenticates its polling and response channel with its separate token; a phone cannot poll that channel with its device grant. Requests are authorized and replay-checked on the PC. A relay can delay, drop, replay, or deny service; it cannot approve, read, or change authenticated contents. It sees IPs, connection times, room and device identifiers, and packet sizes. This transport does not provide forward secrecy.

No relay hosting or push service has been deployed. Closed-app wake-ups (APNs/FCM) are not implemented. The device UI and native networking still require real-device proof.
