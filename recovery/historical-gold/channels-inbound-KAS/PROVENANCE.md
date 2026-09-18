# PROVENANCE — channels-inbound-KAS

- **Source:** dangling KAS-049 lane 175d378d (pre-rebase b708fd38) in vool-checkout
- **Historical path:** core/external_ingress.py, connector_awareness*.py, discord_recent_*.py + relay/bridge_workers/* (discord_gateway_ingress/state, discord_command_parser, telegram bridges, webhook_ingress)
- **Maturity:** SUBSTANTIAL (46-commit real-time websocket Discord Gateway + governed connectors; canonical is polling-only)
- **Historically wired:** lane only, never merged
- **Historically tested:** yes (~5k lines of tests on-lane)
- **Current VOOL equivalent:** WEAKER — canonical has outbound-only relay/bridge_workers/discord_bridge.py
- **Recovery recommendation:** PRESERVE + ANALYZE ONLY. **KAS owns external channels/integrations** — do NOT wire into VOOL if it violates the VOOL/KAS boundary. Hand to the KAS lane.
- **Owner:** KAS lane (external channels)
- **Runtime status:** NOT REGISTERED

Vault copy is PRESERVATION only — NOT imported or registered by the runtime (guarded by tests/test_recovery_vault_isolation.py). Identity/machine paths scrubbed.
