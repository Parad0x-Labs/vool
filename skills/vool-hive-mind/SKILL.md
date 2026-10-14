---
name: vool-hive-mind
description: Local-first AI agent with autonomous research, Brain Hive mesh, persistent memory, and sandboxed tool execution
version: 0.3.0
license: MIT
author: sls_0x
status: alpha
type: external_bridge
api_url: http://127.0.0.1:11435
ollama_port: 11434
id: vool-hive-mind
risk-class: read_only
task-families: []
capability-families: [hive, network, orchestration]
tool-intents: []
permitted-tools: []
prerequisites: []
expected-outputs: [bridge_guidance]
verification: [evidence_cited]
stopping-conditions: ["stop when the bridge/mesh question is answered from the document"]
incompatible-with: []
priority: 100
---
# Vool Hive Mind

## What This Agent Does

Vool is a local-first AI agent that runs entirely on your machine via Ollama.
It provides persistent memory, sandboxed code execution, and multi-platform relay.
Brain Hive (a peer-to-peer research mesh) is an experimental research feature.

## Install

Install VOOL from its release package (see `docs/INSTALL.md`). Brain Hive and the
peer mesh are research features and stay off until they are explicitly enabled.

## Capabilities

| Capability | Description | Status |
|-----------|-------------|--------|
| **Chat** | Conversational AI with persistent memory | Stable |
| **Brain Hive** | Distributed task queue — publish, claim, deliver, grade | Research, off by default (`VOOL_RESEARCH_NETWORKING=1`) |
| **Tool Execution** | Create folders, write files, scaffold projects | Stable |
| **Sandbox** | Run code in a restricted environment. Kernel-enforced no-network on macOS/Linux (and WSL2); native Windows uses the static command guard only | Stable (kernel sandbox needs macOS/Linux/WSL2) |
| **Web Search** | Live web search via SearXNG or direct adapters | Opt-in, OFF in the local-only profile (enable on a non-local-only profile and/or `VOOL_ENABLE_WEB=1`) |
| **Research** | Autonomous web research with evidence scoring | Opt-in (rides on Web Search above) |
| **Entity Lookup** | Who-is/what-is queries with web verification | Opt-in (rides on Web Search above) |
| **Weather/Price** | Real-time weather, crypto prices, commodity prices | Opt-in (rides on Web Search above) |
| **Discord Relay** | Full bot integration with channel routing | Stable |
| **Telegram Relay** | Bot API with group chat support | Stable |
| **P2P Mesh** | NAT traversal, DHT discovery, encrypted streams | Research, off by default (`VOOL_RESEARCH_NETWORKING=1`) |
| **LoRA Training** | Fine-tuning adapter for local models | Experimental |

`GET /api/runtime/capabilities` reports the live per-feature status for the running
profile, including whether web lookup is enabled and whether the kernel job sandbox
is available on this host.

## Local API

The API server on port 11435 is OpenAI-compatible and serves the local chat.

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/chat` | POST | Send a message, get a response |
| `/health` | GET | Runtime health check |
| `/v1/chat/completions` | POST | OpenAI-compatible chat completions |

### Example: Chat

```bash
curl -X POST http://127.0.0.1:11435/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Check hive tasks"}'
```

### Example: OpenAI-Compatible

```bash
curl -X POST http://127.0.0.1:11435/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "vool",
    "messages": [{"role": "user", "content": "What is the weather in London?"}]
  }'
```

## Hardware Requirements

Vool auto-selects the best model for your hardware:

| Min VRAM | Min RAM | Model Selected | Quality |
|----------|---------|---------------|---------|
| 48 GB | 80 GB | qwen2.5:72b | Excellent |
| 20 GB | 48 GB | qwen2.5:32b | Very good |
| 10 GB | 24 GB | qwen2.5:14b | Good |
| 4 GB | 12 GB | qwen2.5:7b | Adequate |
| 2 GB | 6 GB | qwen2.5:3b | Basic |
| 0 GB | 0 GB | qwen2.5:0.5b | Minimal |

Override: `VOOL_OLLAMA_MODEL=mistral:7b` (any Ollama model works).

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `VOOL_OLLAMA_MODEL` | auto-detected | Force a specific Ollama model |
| `VOOL_HOME` | `~/.vool_runtime` | Runtime data directory |
| `VOOL_PUBLIC_HIVE_WATCH_HOST` | (none) | Brain Hive watcher host |
| `VOOL_CLOUD_API_KEY` | (none) | API key for cloud fallback provider |

## File Locations

| Path | Contents |
|------|----------|
| `~/.vool_runtime/` | Runtime state, memory, identity |
| `./config/default_policy.yaml` | Behavior policy |
| `./config/model_providers.sample.json` | Provider config template |

## Troubleshooting

```bash
# Post-install health check
python3 installer/doctor.py

# Verify Ollama is serving
curl http://127.0.0.1:11434/api/tags

# Verify VOOL API is running
curl http://127.0.0.1:11435/health

```
