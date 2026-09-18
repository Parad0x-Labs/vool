# Trust & Security Model

How VOOL handles your data, what leaves your machine, and what you can audit.

---

## Threat Model

### What VOOL protects against

| Threat | Mitigation |
|--------|-----------|
| **Data exfiltration** | Nothing leaves your machine unless you explicitly enable mesh sharing. All outbound data requires policy approval. |
| **Malicious code execution** | Sandboxed runner with network guard, filesystem restrictions, and resource limits. Execution gate requires policy approval for shell/network/write operations. |
| **Untrusted peers** | New knowledge shards start untrusted. Remote shards require schema validation and signature verification. Freshness audits challenge holders. |
| **Man-in-the-middle** | TLS required on all non-loopback connections. Signed write envelopes with nonce replay protection and route-to-actor binding. |
| **Prompt injection via shards** | Raw code, filesystem paths, usernames, and tokens are stripped from shared knowledge. Abstract resolution patterns only. |
| **Identity spoofing** | Ed25519 cryptographic node identity. Capability tokens gate sensitive operations. |

### What VOOL does NOT protect against

- Vulnerabilities in Ollama or your chosen LLM model
- Hardware-level attacks on your machine
- Social engineering targeting you as the operator
- DDoS against your individual node (mitigate at network level)
- Bugs in alpha-stage code (this is early software — report issues)

### Trust boundaries

```
┌──────────────────────────────────┐
│         YOUR MACHINE             │
│  ┌──────────┐  ┌──────────────┐  │
│  │ Ollama   │  │ VOOL Agent  │  │  ← Full trust. Your data lives here.
│  │ (LLM)    │  │ (Core)       │  │
│  └──────────┘  └──────────────┘  │
│  ┌──────────────────────────────┐ │
│  │ Local SQLite + Memory        │ │  ← Encrypted at rest (OS-level).
│  └──────────────────────────────┘ │
├──────────────────────────────────┤
│         LAN / INTERNET           │
│  ┌──────────┐  ┌──────────────┐  │
│  │ Meet     │  │ Peer Agents  │  │  ← Partial trust. Verified by signatures
│  │ Nodes    │  │              │  │    and possession challenges.
│  └──────────┘  └──────────────┘  │
├──────────────────────────────────┤
│         OPTIONAL CLOUD           │
│  ┌──────────────────────────────┐ │
│  │ OpenAI / Anthropic fallback  │ │  ← Explicit opt-in only. Prompts sent
│  └──────────────────────────────┘ │    to cloud provider's API.
└──────────────────────────────────┘
```

---

## What Leaves Your Machine

### By default (fresh install, no config changes)

**Nothing.** The agent talks to local Ollama only. Zero network calls.

### When you enable mesh features

| Data type | When it leaves | Where it goes | Can you disable it? |
|-----------|---------------|---------------|-------------------|
| Presence heartbeat | Mesh enabled | Meet nodes | Yes — disable mesh in policy |
| Task claims | You claim a hive task | Brain Hive peers | Yes — don't claim tasks |
| Research artifacts | You deliver hive work | Brain Hive peers | Yes — keep research local |
| Knowledge shards | Share scope = public | Mesh peers | Yes — set `share_scope: local_only` |
| LLM prompts | Cloud fallback enabled | Cloud provider API | Yes — disable cloud fallback |

### What NEVER leaves

- Your conversation history
- Your personal preferences and user profile
- Your local file system contents
- Your SSH keys, API keys, or credentials
- Your identity private key (only the public key is shared)
- Audit logs

---

## Safe Defaults

Every fresh install starts with these policies (`config/default_policy.yaml`):

| Setting | Default | Meaning |
|---------|---------|---------|
| `persona_core_locked` | `true` | Agent personality cannot be overridden by prompts |
| `sandbox_enabled` | `true` | Code execution runs in restricted environment |
| `require_approval_for` | `[shell, network, filesystem_write]` | Dangerous operations need explicit approval |
| `shards.new_shards_start_untrusted` | `true` | Remote knowledge must prove itself |
| `shards.require_signature_for_remote_shards` | `true` | Unsigned shards are rejected |
| `shards.allow_raw_code_in_shards` | `false` | Code cannot hide in shared knowledge |
| `shards.allow_paths_in_shards` | `false` | Filesystem paths stripped from shared knowledge |
| `shards.allow_tokens_in_shards` | `false` | Credentials stripped from shared knowledge |

---

## Audit Log

VOOL maintains a local audit log of security-relevant events. Every operation that crosses a trust boundary is recorded.

Logged events include:
- Execution gate decisions (approved/denied/escalated)
- Outbound network requests
- Knowledge shard creation, promotion, and replication
- Peer authentication attempts
- Model provider switches
- Sandbox execution results

View audit events:
```bash
# Recent audit entries
python -m ops.swarm_trace_report

# Full audit database is in your local data directory
sqlite3 .vool_local/audit.db "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT 50"
```

---

## Crash & Recovery

| Scenario | Behavior |
|----------|----------|
| Agent process crash | Restarts clean. Persistent memory and dialogue history survive in SQLite. No data loss. |
| Mid-task crash | Task state is journaled. On restart, incomplete tasks can be resumed or re-claimed. |
| Database corruption | SQLite WAL mode provides crash-safe writes. Backup your `.vool_local/` directory for extra safety. |
| Key loss | Generate a new identity with `python -m network.signer --new-identity`. Old peer reputation is lost but data is not. |
| Meet node down | Agent retries other seed nodes. Falls back to local-only mode. No data loss. |

---

## Identity & Key Rotation

Each VOOL node has an Ed25519 keypair stored in `.vool_local/keys/`:

```bash
# View your node identity
python -m network.signer --show-identity

# Rotate to a new keypair (old peer reputation is lost)
python -m network.signer --new-identity
```

Key rotation creates a new peer identity. The old identity is not revoked on the network (there is no central authority to revoke it) — it simply stops appearing in presence heartbeats and is eventually pruned by freshness policies.

---

## Reporting Vulnerabilities

See [SECURITY.md](../SECURITY.md) for responsible disclosure process. Use GitHub Security Advisories or contact the maintainers directly — do not open public issues for security bugs.
