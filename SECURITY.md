# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in VOOL, **do not open a public issue.**

Instead, use one of these channels:

1. **GitHub Security Advisories** (preferred):
   https://github.com/Parad0x-Labs/vool/security/advisories/new

2. **Email**: Reach out to the maintainers via the contact listed on the [Parad0x-Labs GitHub org](https://github.com/Parad0x-Labs).

We will acknowledge receipt within 72 hours and aim to provide a fix or mitigation plan within 14 days.

## Scope

### In scope

- The Brain Hive Watch server (`apps/brain_hive_watch_server.py`)
- API endpoints exposed by the Vool runtime
- Input validation and sanitization in any public-facing route
- Authentication and authorization logic
- Dependency vulnerabilities

### Out of scope

- Vulnerabilities in third-party services (Ollama, OpenClaw) that are not caused by our integration
- Social engineering attacks
- Denial-of-service attacks against individual operator deployments
- Issues in forks or modified versions of this code

## Reporting a NON-security bug safely

The product ships an opt-in bug reporter (`/api/bug-report/*`, `vool bug-report` CLI) that builds
a **local sanitized draft first**: deterministic model-free redaction, allowlist reconstruction,
an outbound secret scan, an exact-bytes preview you can trim field-by-field, and submission only
after you approve the exact bytes — with a durable local receipt of what left the machine.
See `docs/SAFE_BUG_REPORTER.md`. Prefer it for ordinary bugs; it keeps secrets,
paths, and conversation content off the wire by construction. Security vulnerabilities still go
through the private channels above, never a public issue.

## Supported Versions

Only the latest `main` branch is actively maintained. We do not backport fixes to older tags or branches.

## Disclosure

We follow coordinated disclosure. We will credit reporters (unless they prefer to remain anonymous) when the fix is released.
