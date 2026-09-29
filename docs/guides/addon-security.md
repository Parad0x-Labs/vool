# Discover skills and Eyebrow checks

Skills and Plugins has three views: **Installed**, **Discover**, and **Security integrations**. Discover starts with two public, pinned standalone instruction skills. Search ignores case and common separators; category and sort controls narrow the list. Source, licence, revision and requirements are visible before downloading.

## First use

1. Save an Eyebrow API key in Security integrations, also available under Settings → API Keys. The existing credential store owns the key. Saving does not test the connection or scan anything; Test contacts Eyebrow explicitly.
2. In Discover, choose a skill and approve downloading its public content and sending that content to Eyebrow. This consumes the service's scan allowance according to your plan. No automatic retries occur.
3. Read the returned report. A successful HTTP response alone is not a clean result. This initial catalogue permits installation only with a complete `pass` report, no findings and no policy violations. Reject discards the staged download without installing it.
4. Accept installation separately. VOOL preserves the licence, installs through its existing plugin lifecycle, verifies the exact reviewed package and enables the skill. Existing permissions still govern every action it might suggest.

A scan is a vendor's assessment of specific bytes, not a guarantee of safety, compatibility or answer quality. Catalogue inclusion is not certification of the publisher. The Anthropic brand guide is explicitly for Anthropic's visual identity, not VOOL's brand.

## Boundaries

Only shipped catalogue entries are accepted: fixed public GitHub revisions, SHA-256-pinned skill and licence files. There is no arbitrary URL, private repository, archive extraction, install script or executable/MCP plugin importer in this version. Only the standalone SKILL.md is sent to Eyebrow; local chats, workspace files, credentials and licence contents are not submitted for scanning.

Downloads remain inert until review and acceptance. Locally authenticated receipts bind the report to the pinned content. Installation refuses expired reviews (24 hours), modified staged files and existing target directories. The runtime checks lifecycle state and reviewed bytes before adding a managed skill to model context. Added files, symlinks or changed content make it unavailable. Guidance is inserted whole or explicitly recorded as omitted for insufficient prompt budget; the scanned instructions are not silently truncated.

These checks do not isolate malicious code already executing as the operating-system user. They do not extend to pre-existing manually installed plugins. No new execution permission or wallet authority is granted by a scan receipt.

## API and failure handling

`GET /api/addons` returns catalogue and local status without a connection test or scan. `POST /api/addons` supports `save_key`, `remove_key`, `test_key`, `scan`, `install` and `reject`. Mutations require loopback, JSON and same-origin browser requests; only operation-specific fields are accepted. Caller-provided source URLs and paths are refused.

The client uses Eyebrow v1's inline skill source request at `/v1/scan`, with bearer authentication, a fixed HTTPS origin, no redirects, bounded response size and no automatic retries. `/v1/version` is used for the explicit connection test. API reference: https://eyebrow.cc/api/reference .

Typical typed failures include:

| Code | Meaning / recovery |
| --- | --- |
| `eyebrow_key_missing` / `eyebrow_unauthorized` | Save or replace the key. |
| `eyebrow_quota` | Review the service allowance before retrying. |
| `eyebrow_timeout` / `eyebrow_unreachable` | No automatic retry; a timed-out scan may still consume allowance. |
| `eyebrow_invalid_report` / `eyebrow_no_coverage` | Evidence is incomplete; nothing is enabled. |
| `addon_source_changed` / `addon_changed` | Pinned or reviewed bytes differ; do not activate them. |
| `review_untrusted` / `review_expired` | Obtain a new review before installation. |
| `addon_already_installed` | Existing files were preserved. Automatic replacement is not implemented. |

## Current limitations and verification

There is no automatic update, arbitrary catalogue importer or in-place repair/reinstall workflow for an existing changed pack. Pending review recovery after an app restart is not exposed in the UI. New interface strings currently have English fallback; complete translations are not claimed.

Automated tests exercise the real lifecycle, skill loading, API handlers and served browser UI with explicitly mocked public downloads and Eyebrow replies. They prove runtime and interface behavior, not live Eyebrow availability or model competence. A real key-backed scan and native packaged-app acceptance remain release checks. No key or paid service call is needed to run these tests.
