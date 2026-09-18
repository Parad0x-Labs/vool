# Web-search BYOK — paste a key, live search gets better

A user pastes a search API key into Settings. VOOL recognises which service it belongs to, checks it
with one real search, and from then on that provider answers live lookups first. With no key the
runtime behaves exactly as before, using its built-in keyless search.

This is the search-side twin of the cloud-LLM BYOK flow, and it deliberately mirrors it:
`core/search_providers.py` is to search what `core/cloud_providers.py` is to models — one frozen
table that drives the credential slot, the request, the settings list and the probe.

## The pieces

| File | Role |
|---|---|
| `core/search_providers.py` | The provider table + `detect_provider` / `normalize_key` / `keyed_providers`. The only place provider facts live. |
| `tools/web/search_api_client.py` | One generic client. Builds the authed request from the table, sends it through the egress door, maps the answer to hits. No per-provider branches. |
| `core/search_connection_state.py` | The Test probe (a real minimal search) and the presence-only rows the settings list renders. |
| `tools/web/web_research.py` | Chain integration: `keyed_search_api_providers()` gate + `_with_keyed_search_apis()` ordering + one dispatch block. |
| `core/web/api/service.py` | `POST /api/settings/credentials` (search branch), `GET /api/search/providers`, `POST /api/search/test`, `POST /api/search/detect`. |
| `core/vool_chat_page.py` | The "Web search key" settings section, driven entirely by `/api/search/providers`. |

## Design decisions worth keeping

**A pasted key is the permission.** `policy_engine.allowed_web_engines()` is a closed allowlist, and
its own comment records that an installed user policy file *will* silently strip an engine added
after that file was written — deliberately, because it is the permission boundary for engines the
runtime reaches for on its own. Applying that to a key-backed provider would mean a user pastes a
key, sees "saved", and gets nothing, with no way to find out why. So key-backed providers are
admitted by key presence (`_with_keyed_search_apis`), and every other control still applies: the
per-turn remote-fetch veto, the egress door's accounting, the closed credential-slot whitelist.

**One outbound door.** The client reaches the network only through
`core.remote_fetch_policy.open_remote`, so a vetoed turn fails closed and every call is counted in
the turn's `web_calls`. A provider that opens its own socket is how, on 2026-08-18, a turn that
forbade remote fetching still reached SearXNG and reported zero calls.
`tests/test_search_api_byok.py` asserts this structurally (AST) as well as behaviourally.

**Detection may guess, but never silently.** Four providers have distinguishing key prefixes; three
do not. A key with no recognised prefix is reported ambiguous with candidates and the UI *asks*.
Storing a key in the wrong slot yields a provider that fails auth forever with a key the user knows
is good — the worst outcome available here.

**The Test button runs the real path.** The probe is one real minimal search through the same
client, not a cheaper health endpoint, so a green Test proves the path that actually answers
questions. It is rate-limited per provider so repeated clicks cannot burn a free tier, and it sends
a fixed neutral query — never anything from the conversation.

**Failures are classified.** `unauthorized` / `rate_limited` / `quota_exhausted` / `unreachable` /
`bad_shape` reach the turn notes and the UI as distinct states, because "this key is dead" must
never read the same as "the web had nothing" — a distinction the search stack could not make at all
before this change.

## Provider table — verified 2026-08-19

All endpoints, auth headers, result paths and prefixes below were checked against live provider
documentation on 2026-08-19. Re-verify before trusting them later; this space moves in weeks.

| Provider | Method + endpoint | Auth | Query / count | Results path | Title / URL / snippet | Key prefix | Free tier |
|---|---|---|---|---|---|---|---|
| Serper | POST `google.serper.dev/search` | header `X-API-KEY` | `q` / `num` | `organic[]` | title / **link** / snippet | *none* | 2,500 free, no card |
| Brave | GET `api.search.brave.com/res/v1/web/search` | header `X-Subscription-Token` | `q` / `count` | `web.results[]` | title / url / description | `BSAI` | **card required** (free tier ended Feb 2026) |
| Tavily | POST `api.tavily.com/search` | Bearer | `query` / `max_results` | `results[]` | title / url / content | `tvly-` | 1,000/month |
| Exa | POST `api.exa.ai/search` | header `x-api-key` | `query` / — | `results[]` | title / url / highlights,text | *none* | $10/month credit, no card |
| Firecrawl | POST `api.firecrawl.dev/v2/search` | Bearer | `query` / `limit` | `data.web[]` | title / url / description | `fc-` | 1,000/month, no card |
| Jina | POST `s.jina.ai/` | Bearer | `q` / `num` | `data[]` | title / url / description | `jina_` | free tokens on signup |
| You.com | POST `ydc-index.io/v1/search` | header `X-API-Key` | `query` / `count` | `results.web[]` | title / url / description | *none* | $100 signup credit |

Two facts could **not** be confirmed from primary documentation and are handled conservatively
rather than guessed:

- **Exa's result-count field name.** The request omits a count field entirely and takes the provider
  default; `max_hits` still trims the parsed list. Sending a wrong field name risks a 422 on every
  search, which would look like a broken key.
- **Exact invalid-key status/body for Brave, Serper, Jina and You.com.** The client classifies by
  HTTP status (401/403 → `unauthorized`, 429 → `rate_limited`, 402 → `quota_exhausted`), which does
  not depend on any provider's error body shape.

Jina answers in Markdown unless `Accept: application/json` is sent; the client sends that header for
every provider, so it is satisfied centrally rather than as a per-provider special case.

## Adding a provider

Add one entry to `SEARCH_PROVIDERS`. Nothing else changes: the settings list, auto-detection, the
credential slot, the Keychain re-index, the probe and the search chain all read the table. If the
provider needs a wire shape the config cannot express, extend `SearchProviderConfig` — do not add a
branch to the client.

## Known limits

- A key reaches the running app only after the daemon restarts (the chain reads the store per turn,
  but the app must be running the code that has this feature).
- Header names are sent capitalised by `urllib` (`X-api-key`); HTTP headers are case-insensitive and
  all seven providers accept this, but it is worth knowing if a future provider is strict.
- Only web search is wired. Provider extras (news, images, Tavily's synthesized `answer` field,
  Exa's full `text`) are not consumed yet.
