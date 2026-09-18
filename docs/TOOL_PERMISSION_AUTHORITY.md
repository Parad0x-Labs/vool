# Tool permission authority — the flow, and what it does not yet cover

Status: implemented + tested at `build/tool-permission-p0-20260831`, base
`bb338f0947613c6633c8daabf490c5c8fcfee01d`. Not driven against a live daemon; see
[Remaining gaps](#remaining-gaps).

---

## The defect this closes

`execute_tool_intent` consulted the permission controller conditionally:

```python
permission = (
    decide_tool_call(...) if mode_policy_is_active(source_context) else None
)
```

`mode_policy_is_active` answered False whenever the context named no `operating_mode` **and** no
server-side session record existed. Every such call went straight to dispatch with no decision
taken. Measured on the base commit, through the real executor and the real handlers:

| Call | `decide_tool_call` calls | Result |
|---|---|---|
| `workspace.write_file` (no mode) | `[]` | file created on disk |
| `sandbox.run_command` `touch …` (no mode) | `[]` | exit 0, marker file created |

The compatibility seam could not distinguish a background maintenance task from a chat turn whose
mode failed to arrive, and it granted both of them everything.

**Defaulting the missing mode to AUTO does not fix this.** Auto *allows* `CREATE_FILES`,
`MODIFY_FILES`, and `RUN_SIDE_EFFECTING_COMMANDS`, so both rows above would still execute unasked.
The default has to be MANUAL.

---

## The authority flow, exactly

```
HTTP /api/chat  ──► resolve_effective_mode(session, requested_mode, …)
                      │  1. explicit valid mode        → set_active_mode(...)  [records + revisions it]
                      │  2. else existing session record → that record
                      │  3. else                        → MANUAL
                      │  bypass_permissions at any step → requires a live server-side grant,
                      │                                   else MANUAL + downgraded_from
                      ▼
                  source_context["operating_mode"]           (stamped on EVERY turn)
                  source_context["operating_mode_revision"]
                      │
                      ▼
execute_tool_intent ──► _intent_is_dispatchable(intent)?
                      │      no  → "unsupported" — never put to the controller, never dispatched
                      │      yes ↓
                      ├──► decide_tool_call(...)          ◄── UNCONDITIONAL
                      │      │
                      │      ├─ exact-approval token matches this call → ALLOW
                      │      ├─ task/project grant matches             → ALLOW
                      │      ├─ project permissions deny               → DENY
                      │      ├─ typed internal scope covers ALL actions→ ALLOW
                      │      └─ MODE_PERMISSION_MATRIX[mode][actions]  → ALLOW / PROMPT / DENY
                      ▼
                  DENY            → status "blocked_by_mode",   executed=False
                  REQUIRE_APPROVAL→ status "pending_approval",  executed=False
                  ALLOW           → dispatch
```

### Effective mode

`active_mode_state` / `effective_mode_state` is the single reader. It already failed closed to
MANUAL; what changed is that nothing branches around it any more.

- Missing mode → MANUAL.
- Unparseable mode (`"super_auto_do_whatever"`, `17`, `None`) → MANUAL.
- `"bypass"` — a legacy alias the ingress does not accept — → MANUAL.
- A context claiming `operating_mode="bypass_permissions"` with no valid grant → MANUAL.
- A grant that expires or is revoked mid-task → MANUAL on the very next call.

### Per-mode behaviour at the executor

| Mode | reads | workspace write | side-effecting command |
|---|---|---|---|
| Manual (also: missing/invalid) | allowed | `pending_approval` | `pending_approval` |
| Review edits | allowed | `pending_approval` (with diff preview) | `pending_approval` |
| Plan | allowed | `blocked_by_mode` | `blocked_by_mode` |
| Auto | allowed | allowed; **overwrite** of an existing file prompts | allowed |
| Bypass (valid grant only) | allowed | allowed | allowed; money still prompts |

Auto's contract is unchanged by this work.

### The typed internal scope — the only non-chat exception

Background and internal callers get authority by **asking for it**, never by omitting a mode:

```python
token = grant_internal_authority(
    label="why-this-exists",
    actions={PermissionAction.CREATE_FILES},
    duration_seconds=300,
    session_id="sess-the-job-runs-in",   # any ONE of session_id / task_id / intents / workspace_root
)
source_context["internal_authority_token"] = token
```

Properties:

- **Unguessable** — a `secrets.token_urlsafe(32)` a Python caller must hold. Nothing in a model
  payload or an HTTP body can name one.
- **Narrow** — a call is allowed only when *every* classified action is inside the declared set.
  Anything broader falls through to the ordinary matrix, so a scope minted for one job cannot carry
  a second.
- **Expiring** — `duration_seconds` (1..86400) is REQUIRED. There is no process-lifetime token:
  an action-only bearer that lives until the daemon dies is exactly the shape this refuses, and
  an expired scope decides as if it never existed.
- **Bound** — the scope must name what it is for beyond its actions: a `session_id`, a `task_id`,
  an `intents` set, or a `workspace_root`. Every declared binding is enforced at consult time
  against the decision's own session/task/intent/workspace; a call that is not that thing cannot
  use the scope. A grant with neither an expiry nor a binding is refused at mint time.
- **In-process** — memory only. It dies with the daemon and cannot be replayed after a restart.
- **Ceilinged** — `ACCESS_SECRETS`, `FINANCIAL_ACTION`, `CHANGE_SECURITY_SETTINGS`, and
  `UNKNOWN_SIDE_EFFECT` are stripped at grant time and refused again at consult time. No breadth of
  declaration reaches them.
- **Attributable** — the `label` is written into the permission event record, so an allow taken on
  this path is not anonymous.

### Unknown intents

An intent no handler claims is answered `unsupported` *before* the controller. Asking an operator
to approve `fake.magic` would be a prompt nobody can evaluate — the approval card cannot describe an
effect the runtime has no handler for — and "Manual mode requires approval" is untrue for a tool
that does not exist. This is not a bypass: an intent with no handler cannot reach one.

---

## Remaining gaps

Stated plainly; none of these are closed by this change.

1. **Not driven against a live daemon.** Everything here is evidenced through the real executor,
   real handlers, and the real HTTP dispatch function, in-process. No restart of the packaged app
   and no browser drive was performed, so the end-to-end operator experience of the new
   `pending_approval` turns on the chat surface is **unverified**.

2. **MCP tools are unclassifiable to the matrix.** `mcp.<server>.<tool>` names come from a
   third-party server, so `actions_for_tool` reports `unknown_side_effect`. Consequences today:
   Manual/Review **prompt** with a card that cannot name the effect, and Plan/Auto/Bypass **deny**
   outright. Before this change modeless MCP calls simply ran undecided, so this is a strictly
   safer state — but it is not a usable one. MCP tools need declared `permission_actions` (the
   `RuntimeToolContract` field `actions_for_tool` already reads) before the family is practical in
   Auto. Not attempted here: it is catalog work, which this change was scoped out of.

3. **`UNKNOWN_SIDE_EFFECT` prompts in Manual/Review but denies everywhere else.** That asymmetry is
   an artifact of Manual's prompt set being `all actions − reads − secrets`, not a decision. An
   effect the classifier cannot name is arguably never approvable. Left as-is because changing it
   moves behaviour for every unclassified family at once, MCP included.

4. **Non-HTTP surfaces do not stamp a mode.** These build a `source_context` with no
   `operating_mode` and now resolve to MANUAL per turn:

   | Surface | Site | Effect today |
   |---|---|---|
   | Channel chat (Telegram/Discord) | `apps/vool_chat.py:127` | reads fine; mutations raise an approval the channel has no UI to render |
   | CLI web lane | `apps/vool_cli.py:263` | `web.search`/`web.fetch` allowed; **`browser.render` now prompts** and the CLI cannot answer |
   | null:// protocol | `core/web/api/service.py:3892,3921` | read-only surface; unchanged in practice |

   Each of these is a foreground, operator-initiated turn, so the right repair is for each ingress
   to stamp its own controller-owned mode the way `/api/chat` now does — or, for a lane like
   `vool web --render-url` where the typed command *is* the consent, to hold a typed internal
   scope. Neither was done here: the brief scoped ingress work to HTTP, and widening it would
   change behaviour on surfaces this change has no test coverage for.

5. **`effect_gateway.decide_network_fetch` is dead and fails open.** It calls
   `mode_permission_policy.mode_policy_is_active()` with no argument (it takes one) and
   `decide_tool_call("web.research", {}, …)` positionally (it is keyword-only). Both raise
   `TypeError`, which its own `except Exception` converts to `DECISION_ALLOWED, "gateway consult
   failed open"`. The network-fetch door has therefore never consulted the matrix. Left untouched
   deliberately: repairing the signatures would flip a separate door from always-allow to
   matrix-enforced across every fetch lane, which is a behaviour change well outside a P0 scoped to
   `execute_tool_intent`. It is filed here so it is not rediscovered as a surprise.

6. **`mode_policy_is_active` still exists.** It is now a reporting predicate with a docstring
   forbidding its use as a gate, and its one remaining production caller is the dead code in (5).
   It should be deleted once (5) is repaired.
