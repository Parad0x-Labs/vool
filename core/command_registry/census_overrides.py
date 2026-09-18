"""Reviewed classification overrides for the operator-action census.

Every entry names a mechanically-discovered census id. GENERATED_ADAPTER
entries bind to the registry command that owns the declaration; the legacy
surface forwards through execute_command (wired in this change; verified by
tests/command_registry/test_convergence_adapters.py). EXTERNAL_TRANSPORT_ONLY
entries name the lane that owns the surface and why it is not an operator
command (frozen prose routing, machine/UI feed, satellite, launcher, or an
explicitly out-of-scope lane per the P1 goal exclusions).
"""
from __future__ import annotations

_ADAPTERS: dict[str, str] = {
    # module CLI (python -m core.blackbox)
    "cli-module:core.blackbox:status": "blackbox.status",
    "cli-module:core.blackbox:verify": "blackbox.verify",
    "cli-module:core.blackbox:turns": "blackbox.turns",
    "cli-module:core.blackbox:rollback": "blackbox.rollback",
    # REPL chat commands
    "chat-repl:/summary": "status.show",
    "chat-repl:/status": "status.show",
    # vool CLI leaves
    "cli-vool:summary": "status.show",
    # council routes
    "http:GET:/api/council/runs": "council.runs",
    "http:GET:/api/council/status": "council.status",
    "http:GET:/api/council/events": "council.events",
    "http:GET:/api/council/lock": "council.lock",
    "http:GET:/api/council/scorecard": "council.scorecard",
    "http:POST:/api/council/convene": "council.convene",
    "http:POST:/api/council/stop": "council.stop",
    "http:POST:/api/council/seat": "council.seat",
    "http:POST:/api/council/resume": "council.resume",
    # update routes
    "http:GET:/api/update/status": "update.status",
    "http:POST:/api/update/check": "update.check",
    "http:POST:/api/update/install": "update.apply",
    "http:POST:/api/update/restart": "update.restart",
    # turn-context privacy controls (C13): the GET listing is one command; the
    # POST door fans out to the eight-command family declared below.
    "http:GET:/api/context/pages": "context.pages.list",
    # settings routes
    "http:GET:/api/settings/prefs": "settings.prefs.list",
    "http:POST:/api/settings/prefs": "settings.prefs.set",
    "http:GET:/api/settings/credentials": "settings.credentials.list",
    "http:POST:/api/settings/credentials": "settings.credentials.set",
    # PA Contacts operator surfaces (core.command_registry.groups.contacts_group)
    "http:GET:/api/contacts": "contacts.book.list",
    "http:GET:/api/contacts/detail": "contacts.book.detail",
    "http:GET:/api/contacts/resolve": "contacts.book.resolve",
    "http:GET:/api/contacts/options": "contacts.book.options",
    "http:GET:/api/contacts/suggestions": "contacts.book.suggestions",
    "http:POST:/api/contacts/create": "contacts.book.create",
    "http:POST:/api/contacts/update": "contacts.book.update",
    "http:POST:/api/contacts/delete": "contacts.book.delete",
    "http:POST:/api/contacts/suggestions/accept": "contacts.book.suggestion.accept",
    "http:POST:/api/contacts/suggestions/dismiss": "contacts.book.suggestion.dismiss",
    "http:GET:/api/contacts/import/sources": "contacts.import.sources",
    "http:POST:/api/contacts/import/connect": "contacts.import.connect",
    "http:POST:/api/contacts/import/preview": "contacts.import.preview",
    "http:POST:/api/contacts/import/apply": "contacts.import.apply",
    "http:POST:/api/contacts/import/remove": "contacts.import.remove",
    "http:GET:/api/contacts/operations": "contacts.operations.list",
    "http:GET:/api/contacts/operations/detail": "contacts.operations.detail",
    "http:GET:/api/contacts/credential": "contacts.credential.status",
    "http:POST:/api/contacts/operations/confirm": "contacts.operations.confirm",
    "http:POST:/api/contacts/operations/commit": "contacts.operations.commit",
    "http:POST:/api/contacts/operations/cancel": "contacts.operations.cancel",
    "http:POST:/api/contacts/credential/enroll": "contacts.credential.enroll",
    "http:POST:/api/contacts/credential/change": "contacts.credential.change",
    "http:POST:/api/contacts/credential/reset": "contacts.credential.reset",
    # model routes
    "http:GET:/api/cloud/model": "models.current",
    "http:POST:/api/cloud/model": "models.pin",
    "http:POST:/api/cloud/auto-model": "models.auto",
    # ops surfaces (CP2: the skills + KAS lanes' routes enter the registry)
    "http:GET:/api/skills": "skills.list",
    "http:POST:/api/skills/enable": "skills.set_enabled",
    "http:GET:/api/repoops/sessions": "repoops.sessions",
    "http:GET:/api/repoops/session": "repoops.session",
    "http:POST:/api/repoops/authorize-push": "repoops.authorize_push",
    "http:POST:/api/repoops/authorize-forge-action": "repoops.authorize_forge_action",
    "http:GET:/api/plugins/lifecycle": "plugins.lifecycle.state",
    "http:POST:/api/plugins/lifecycle": "plugins.lifecycle.transition",
    # learning operator surfaces (PB01 hook 4)
    "http:GET:/api/learning/procedures": "learning.list",
    "http:POST:/api/learning/procedures": "learning.list",  # same handler/dispatcher as the GET (the shared dispatch convention)
    "http:POST:/api/learning/procedures/{id}/invalidate": "learning.invalidate",
    "http:DELETE:/api/learning/procedures/{id}": "learning.delete",
    # search routes
    "http:GET:/api/search/providers": "search.providers",
    "http:POST:/api/search/test": "search.test",
    # memory + tasks
    "http:POST:/api/memory/forget": "memory.forget",
    "http:POST:/api/task/recovery": "tasks.recovery",
    # approvals (op-dispatched route: resolve_approval / bypass / mode set)
    "http:POST:/api/mode": "approvals.resolve",
}

_EXTERNAL: dict[str, str] = {
    # -- daemon / service entry points --------------------------------------
    "cli-script:vool-agent": "runtime daemon entry point",
    "cli-script:vool-api": "API server entry point",
    "cli-script:vool-daemon": "runtime daemon entry point",
    "cli-script:vool-meet": "meet satellite entry point",
    "cli-script:vool-watch": "watch satellite entry point",
    "cli-script:vool-benchmark": "developer benchmark tool",
    "cli-vool:up": "OS launcher (starts services)",
    # -- wallet / commercial lane (P1 goal exclusion) -------------------------
    "cli-vool:wallet-init": "wallet lane (goal exclusion: wallet)",
    "cli-vool:wallet-status": "wallet lane (goal exclusion: wallet)",
    "cli-vool:wallet-address": "wallet lane (goal exclusion: wallet)",
    "cli-vool:wallet-export": "wallet lane (goal exclusion: wallet)",
    "cli-vool:wallet-topup-hot": "wallet lane (goal exclusion: wallet)",
    "cli-vool:wallet-move-cold": "wallet lane (goal exclusion: wallet)",
    "cli-vool:wallet-buy-credits": "wallet lane (goal exclusion: wallet)",
    "cli-vool:x402-pay": "wallet lane (goal exclusion: wallet)",
    "cli-vool:credits": "credits lane (goal exclusion: wallet)",
    "cli-vool:spend-freeze": "wallet spend policy (goal exclusion: wallet)",
    "cli-vool:spend-unfreeze": "wallet spend policy (goal exclusion: wallet)",
    "cli-vool:spend-set-cap": "wallet spend policy (goal exclusion: wallet)",
    "cli-vool:spend-policy": "wallet spend policy (goal exclusion: wallet)",
    "http:GET:/api/wallet/info": "wallet lane feed (goal exclusion: wallet)",
    "http:GET:/api/wallet/safety": "wallet safety envelope feed (goal exclusion: wallet)",
    "http:POST:/api/wallet/export": "wallet lane: private-key export behind device presence (goal exclusion: wallet)",
    "http:POST:/api/wallet/approval-method": "wallet lane: PIN / password / device switch (goal exclusion: wallet)",
    "http:GET:/api/credits/balance": "credits lane feed (goal exclusion: wallet)",
    "http:POST:/api/credits/settle": "credits lane (goal exclusion: wallet)",
    # -- web0 / peer lane ------------------------------------------------------
    "cli-vool:web": "performs a web-search turn (model lane action, not a command)",
    "cli-vool:resolve": "web0 peer lane",
    "cli-vool:dial": "web0 peer lane",
    "cli-vool:register": "web0 peer lane",
    "cli-vool:sell-quote": "web0 peer lane",
    "cli-vool:identities": "web0 peer lane",
    "cli-vool:manifest": "web0 capability card (peer lane)",
    "cli-vool:nullpass": "device-tether parked lane",
    "cli-vool:receipts": "web0 anchored proofs (commercial lane); registry receipts.* owns the operator receipts surface",
    "http:GET:/api/web0/resolve": "web0 gateway feed",
    "http:GET:/": "index page transport",
    # The Settings page itself performs no operator action: it serves HTML. Every action it
    # offers is a POST that the registry already owns (settings.prefs.set,
    # settings.credentials.set, models.pin, ...), which is why those appear above as adapters
    # and this does not.
    "http:GET:/settings": "settings page transport",
    "http:POST:/api/null": "web0 lane",
    "http:POST:/gate/unlock": "web0 lane",
    "http:POST:/api/show": "web0 lane",
    "chat-repl:/balance": "credits lane via REPL",
    "chat-repl:/credits": "credits lane via REPL",
    "chat-repl:/dial": "web0 lane via REPL",
    "chat-repl:/quote": "web0 lane via REPL",
    "chat-repl:/resolve": "web0 lane via REPL",
    "chat-repl:/manifest": "web0 lane via REPL",
    "chat-repl:/capabilities": "web0 capability card via REPL",
    "chat-repl:/web": "performs a web-search turn (model lane action)",
    # -- adaptation / training (developer tooling) ------------------------------
    "cli-vool:adaptation-status": "adaptation lane developer tooling",
    "cli-vool:adapt-corpus": "adaptation lane developer tooling",
    "cli-vool:adapt-corpus-import": "adaptation lane developer tooling",
    "cli-vool:adapt-corpus-export": "adaptation lane developer tooling",
    "cli-vool:adapt-job-create": "adaptation lane developer tooling",
    "cli-vool:adapt-jobs": "adaptation lane developer tooling",
    "cli-vool:adapt-evals": "adaptation lane developer tooling",
    "cli-vool:adapt-job-events": "adaptation lane developer tooling",
    "cli-vool:adapt-job-run": "adaptation lane developer tooling",
    "cli-vool:adapt-promote": "adaptation lane developer tooling",
    "cli-vool:adapt-loop-status": "adaptation lane developer tooling",
    "cli-vool:adapt-loop-tick": "adaptation lane developer tooling",
    "cli-vool:adapt-autopilot": "adaptation lane developer tooling",
    "cli-vool:control-sync": "control-plane sync tooling",
    "cli-vool:trainable-base-status": "training lane tooling",
    "cli-vool:stage-trainable-base": "training lane tooling",
    "http:GET:/api/adaptation/status": "adaptation lane feed",
    "http:GET:/api/adaptation/loop": "adaptation lane feed",
    "http:GET:/api/adaptation/jobs": "adaptation lane feed",
    "http:GET:/api/adaptation/job-events": "adaptation lane feed",
    "http:GET:/api/adaptation/evals": "adaptation lane feed",
    "http:POST:/api/adaptation/loop/tick": "adaptation lane (gated internally)",
    # -- bug-report lane (own consent flow; report group = blueprint P2) ---------
    "cli-vool:bug-report": "bug-report pipeline lane",
    "cli-vool:bug-report.draft": "bug-report pipeline lane",
    "cli-vool:bug-report.preview": "bug-report pipeline lane",
    "cli-vool:bug-report.approve": "bug-report pipeline lane",
    "cli-vool:bug-report.submit": "bug-report pipeline lane",
    "cli-vool:bug-report.status": "bug-report pipeline lane",
    "cli-vool:bug-report.receipts": "bug-report pipeline lane",
    "http:GET:/api/bug-report/status": "bug-report pipeline lane",
    "http:GET:/api/bug-report/candidates": "bug-report pipeline lane",
    "http:GET:/api/bug-report/receipts": "bug-report pipeline lane",
    "http:POST:/api/bug-report/draft": "bug-report pipeline lane",
    "http:POST:/api/bug-report/update": "bug-report pipeline lane",
    "http:POST:/api/bug-report/preview": "bug-report pipeline lane",
    "http:POST:/api/bug-report/approve": "bug-report pipeline lane",
    "http:POST:/api/bug-report/submit": "bug-report pipeline lane",
    # -- session portability lane (own typed consent/refusal flow: passphrase
    #    envelope, unknown-signer acknowledgement, import trust law) -------------
    "cli-vool:session-bundle": "session-portability lane (thin shell over core.session_portability.api; settings group = command-centre blueprint follow-up)",
    "cli-vool:session-bundle.preview": "session-portability lane (export preview over the ONE seam)",
    "cli-vool:session-bundle.export": "session-portability lane (signed/encrypted bundle export over the ONE seam)",
    "cli-vool:session-bundle.inspect": "session-portability lane (bundle identity/manifest read over the ONE seam)",
    "cli-vool:session-bundle.import": "session-portability lane (atomic import over the ONE seam)",
    "http:GET:/api/session/bundle/download": "session-portability lane (confined download under session_bundles/)",
    "http:UPLOAD:/api/session/bundle/upload": "session-portability lane (staging upload door; nothing parsed/imported)",
    "http:POST:/api/session/bundle/preview": "session-portability lane (preview door over the ONE seam)",
    "http:POST:/api/session/bundle/export": "session-portability lane (export door over the ONE seam)",
    "http:POST:/api/session/bundle/import": "session-portability lane (import door over the ONE seam)",
    "http:POST:/api/session/bundle/inspect-import": "session-portability lane (inspect+preview door over the ONE seam)",
    # -- certification / installer lane ------------------------------------------
    "cli-vool:install-profile": "installer lane",
    "cli-vool:release-status": "installer lane",
    "cli-vool:model-tool-certification": "certification lane",
    "cli-vool:providers": "local provider registry audit (certification lane)",
    "http:GET:/api/model-tool-certification": "certification lane feed",
    "http:POST:/api/model-tool-certification/run": "certification lane",
    "cli-vool:update": "installer update_cli interactive flow; registry update.* owns the operator commands",
    # -- runtime / UI data feeds (page+poll machinery, not commands) -------------
    "http:GET:/api/runtime/version": "runtime feed",
    "http:GET:/api/runtime/capabilities": "runtime feed",
    "http:GET:/api/runtime/sessions": "runtime feed",
    "http:GET:/api/runtime/events": "runtime Activity feed",
    "http:GET:/api/runtime/changes": "runtime feed",
    "http:GET:/api/runtime/usage": "runtime feed",
    "http:GET:/api/runtime/receipts": "Activity receipts feed",
    "http:GET:/api/runtime/unresolved-effects": "repair-lane feed",
    "http:GET:/api/runtime/control-plane/status": "control-plane aggregate feed",
    "http:GET:/api/runtime/operator-snapshot": "operator snapshot feed",
    "http:POST:/api/runtime/unresolved-effects/resolve": "repair lane (RootCauseContract); folds into the repair group follow-up",
    "http:GET:/api/profile": "profile feed",
    "http:GET:/api/profile/export": "profile export",
    "http:GET:/api/plugins": "plugin catalog feed (lane OFF by default)",
    "http:POST:/api/plugins/enable": "plugin lane (flag OFF by default)",
    "http:GET:/api/setup/state": "first-run setup progress feed (derived; core/setup_progress.py)",
    "http:GET:/api/onboarding/pact": "first-run pact state feed (server authority kept; its chat UI was retired 2026-09-07)",
    "http:GET:/api/onboarding/state": "first-run provider-choice feed (the setup page reads /api/setup/state; kept for the pact authority)",
    "http:GET:/setup": "guided setup page (served like /settings; writes go through existing doors)",
    "http:GET:/api/projects": "workspace projects feed",
    "http:POST:/api/projects": "workspace lane",
    "http:POST:/api/projects/delete": "workspace lane",
    "http:POST:/api/projects/reveal": "workspace lane (OS reveal)",
    "http:POST:/api/projects/emoji": "workspace lane (UI-first)",
    "http:GET:/api/files": "workspace files feed (deliberately read-only)",
    "http:GET:/api/files/raw": "workspace files feed",
    "http:POST:/api/files/open": "workspace lane (OS reveal)",
    "http:GET:/api/oauth/status": "oauth feed",
    "http:GET:/api/connections": "connections feed",
    "http:GET:/api/memory/entries": "memory entries feed",
    "http:GET:/api/cloud/providers": "cloud provider catalog feed",
    "http:GET:/api/cloud/models": "cloud model catalog feed (dropdown)",
    "http:GET:/api/cloud/keys": "credential intelligence lane",
    "http:GET:/api/cloud/diagnostics": "cloud diagnostics feed",
    "http:GET:/api/cloud/status": "cloud status feed",
    "http:POST:/api/cloud/test": "cloud connection probe lane",
    "http:GET:/api/cloud/acceptances": "price-acceptance feed",
    "http:GET:/api/cloud/market-events": "market events feed",
    "http:GET:/api/cloud/price-history": "price history feed",
    "http:GET:/api/model-radar/feed": "model-radar lane feed",
    "http:GET:/api/model-radar/preferences": "model-radar lane",
    "http:POST:/api/model-radar/preferences": "model-radar lane",
    "http:POST:/api/model-radar/dismiss": "model-radar lane",
    "http:POST:/api/model-radar/viewed": "model-radar lane",
    "http:POST:/api/model-radar/try-once": "model-radar lane",
    "http:POST:/api/search/detect": "runtime detection probe",
    # Mobile companion READ feeds. Newly visible once the scanner learned to follow
    # prefix families. Each is a read: a device list, a pairing status, a pairing
    # page, a receipt list, a capability leaf. None performs an operator action, so
    # none needs an owning command -- but they are classified here deliberately and
    # by name, rather than left as LEGACY_UNMIGRATED or hidden behind a wildcard.
    # The mobile lane's WRITE surface is owned: see groups/delegated_routes.py.
    "http:GET:/api/mobile/devices": "mobile companion read feed (device list)",
    "http:GET:/api/mobile/info": "mobile companion read feed (capability leaf)",
    "http:GET:/api/mobile/pairing/challenge": "mobile companion read feed (pairing challenge)",
    "http:GET:/api/mobile/pairing/page": "mobile companion UI page",
    "http:GET:/api/mobile/pairing/status": "mobile companion read feed (pairing status)",
    "http:GET:/api/mobile/receipts": "mobile companion read feed (receipts)",
    # Chat READ feeds, named individually now that `^/api/chat` is anchored. Each is
    # a read of the operator's own chat state, owner-local gated at the route. The
    # six chat ACTIONS under the same prefix are owned commands (groups/delegated_routes),
    # and a NEW `/api/chat/...` surface appears as LEGACY_UNMIGRATED rather than being
    # absorbed by the wildcard this list replaced.
    "http:GET:/api/chat/attachments": "chat read feed (staged attachments)",
    "http:GET:/api/chat/attachments/documents": "chat read feed (attached documents)",
    "http:GET:/api/chat/attachments/limits": "chat read feed (attachment limits)",
    "http:GET:/api/chat/attachments/preview": "chat read feed (attachment preview)",
    "http:GET:/api/chat/dictation": "chat read feed (dictation availability probe)",
    "http:GET:/api/chat/export": "chat read feed (transcript export)",
    "http:GET:/api/chat/history": "chat read feed (transcript)",
    "http:GET:/api/chat/pins": "chat read feed (pinned messages)",
    "http:GET:/api/chat/proof": "chat read feed (proof chip)",
    "http:GET:/api/chat/queue": "chat read feed (queued messages)",
    "http:GET:/api/chat/sessions": "chat read feed (session list)",
    # Lane surfaces that entered with the calendar/notes, email, notifications-centre,
    # money-contract, usepod-discovery, bug-report, school and model/tooling merges.
    # Classified 2026-09-18 when the census pin flagged all 54 as LEGACY_UNMIGRATED:
    # each entry names its owning lane and its class honestly. The READ rows are feeds
    # and pages; the WRITE rows are lane-owned operator surfaces whose consent doors
    # live in their own lanes (loopback + their own approval/confirmation gates) --
    # none has been promoted to a registry command yet, and that promotion remains
    # open follow-up work rather than something this classification claims.
    "http:GET:/api/notifications": "notifications lane read feed (centre inbox)",
    "http:GET:/api/notifications/preferences": "notifications lane read feed (preferences)",
    "http:GET:/api/notifications/native/status": "notifications lane read feed (native helper status)",
    "http:POST:/api/notifications/action": "notifications lane operator action (inbox row action)",
    "http:POST:/api/notifications/preferences": "notifications lane operator action (preferences)",
    "http:POST:/api/notifications/native/outbox": "notifications lane operator action (native outbox drain)",
    "http:POST:/api/notifications/native/report": "notifications lane operator action (native delivery report)",
    "http:POST:/api/notifications/native/test": "notifications lane operator action (native test delivery)",
    "http:POST:/api/notifications/native/authorize": "notifications lane operator action (native authorization)",
    "http:GET:/api/calendar/accounts": "calendar lane read feed (accounts)",
    "http:GET:/api/calendar/alerts": "calendar lane read feed (alerts)",
    "http:GET:/api/reminders": "calendar lane read feed (reminders)",
    "http:POST:/api/calendar/alerts/policy": "calendar lane operator action (alerts policy)",
    "http:POST:/api/calendar/sync": "calendar lane operator action (account sync)",
    "http:POST:/api/calendar/accounts/add": "calendar lane operator action (account add)",
    "http:POST:/api/calendar/accounts/discover": "calendar lane operator action (account discovery)",
    "http:POST:/api/calendar/accounts/select": "calendar lane operator action (account selection)",
    "http:POST:/api/calendar/accounts/opt-in": "calendar lane operator action (projection opt-in)",
    "http:POST:/api/calendar/accounts/disconnect": "calendar lane operator action (account disconnect)",
    "http:POST:/api/calendar/accounts/reconnect": "calendar lane operator action (account reconnect)",
    "http:GET:/api/email/accounts": "email lane read feed (accounts)",
    "http:GET:/api/email/recovery": "email lane read feed (store recovery surface)",
    "http:POST:/api/email/recovery/acknowledge": "email lane operator action (recovery acknowledgement)",
    "http:POST:/api/email/recovery/check": "email lane operator action (recovery check)",
    "http:GET:/api/cloud/usepod/discovery": "usepod lane read feed (discovery)",
    "http:GET:/api/cloud/usepod/x402/unresolved": "usepod lane read feed (unresolved x402 state)",
    "http:POST:/api/cloud/usepod/*": "usepod lane operator surface family (its own spend/consent gates)",
    "http:GET:/api/discovery": "provider discovery lane read feed",
    "http:POST:/api/discovery/refresh": "provider discovery lane operator action (refresh)",
    "http:GET:/api/models/local": "model registry read feed (local models)",
    "http:GET:/api/cloud/spend-limits": "effect-budget lane read feed (spend limits)",
    "http:POST:/api/models/local/register": "model registry operator action (local model registration)",
    "http:POST:/api/plugins/rescan": "plugin catalog operator action (rescan)",
    "http:GET:/api/intake/quarantine/list": "intake lane read feed (quarantine list)",
    "http:GET:/api/bug-report/destination": "bug-report lane read feed (destination state)",
    "http:POST:/api/bug-report/export": "bug-report lane operator action (local sanitized export)",
    "http:POST:/api/bug-report/revoke": "bug-report lane operator action (draft revocation)",
    "cli-vool:bug-report.export": "bug-report lane operator action (local sanitized export)",
    "cli-vool:bug-report.revoke": "bug-report lane operator action (draft revocation)",
    "cli-vool:bug-report.destination": "bug-report lane read (destination state)",
    "http:GET:/school": "school satellite UI page",
    "http:GET:/school/student": "school satellite UI page",
    "http:GET:/school/api/state": "school satellite read feed (state)",
    "http:POST:/school/api/*": "school satellite operator surface family",
    "http:GET:/chat-assets/*": "chat assets read feed (static bundle family)",
    "http:GET:/api/money/contract": "money contract lane read feed (contract)",
    "http:GET:/api/money/funding": "money contract lane read feed (funding)",
    "http:GET:/api/money/grants": "money contract lane read feed (grants)",
    "http:GET:/api/money/liabilities": "money contract lane read feed (liabilities)",
    "http:GET:/api/money/liabilities/{id}": "money contract lane read feed (one liability)",
    "http:GET:/api/money/projection": "money contract lane read feed (projection)",
    "http:GET:/api/money/rules": "money contract lane read feed (rules)",
    "http:POST:/api/money/grants/revoke": "money contract lane operator action (grant revocation)",
    "http:POST:/api/money/reconcile": "money contract lane operator action (reconcile)",
}

# frozen prose demand-routing families + REPL chrome are EXTERNAL by rule in
# census.py (_default_classification); launchers likewise.

# ---------------------------------------------------------------------------
# Adapter FAMILIES — one transport surface whose typed authority is several
# commands. A single POST path can carry several distinct operator actions with
# different effects classes (a read, a reversible mutation, an irreversible
# erasure); collapsing them into one command would have to under-declare one of
# them. The census records the entry command in ``registry_command_id`` and the
# whole family in the note, and ``census_check`` verifies EVERY member exists —
# so a member deleted from the registry is a finding, not a silent narrowing.
# ---------------------------------------------------------------------------

_ADAPTER_FAMILIES: dict[str, tuple[str, ...]] = {
    # One POST under each prefix is ONE registered command (S-P6); the table that binds
    # them lives in core/web/api/onboarding_endpoints.py (_PACT_POST_PATHS).
    "http:POST:/api/onboarding/*": (
        "first_run.choice.local_only",
        "first_run.pact.advance",
        "first_run.pact.begin",
        "first_run.pact.boundary.set",
        "first_run.pact.denial.claim",
        "first_run.pact.facts.forget",
        "first_run.pact.facts.set",
        "first_run.pact.hide",
        "first_run.pact.name",
        "first_run.pact.reset",
        "first_run.pact.skip",
        "first_run.pact.task.claim",
        "onboarding.choice",
        "onboarding.reset",
    ),
    "http:POST:/api/intake/*": (
        "intake.begin",
        "intake.classify",
        "intake.complete",
        "intake.preview",
        "intake.verify",
    ),
    "http:POST:/api/context/pages": (
        "context.pages.archive",
        "context.pages.bump_generation",
        "context.pages.erase",
        "context.pages.pin",
        "context.pages.recall",
        "context.pages.release_pin",
        "context.pages.supersede",
        "context.pages.withhold",
    ),
}


# The delegated-route families the repaired scanner now discovers. Bound from the
# group's own declaration table, so a route declared there and a route classified
# here cannot drift apart.
def _delegated_route_adapters() -> dict[str, str]:
    try:
        from core.command_registry.groups.delegated_routes import (
            chat_action_command_map,
            inline_route_command_map,
            route_command_map,
        )

        merged = route_command_map()
        merged.update(inline_route_command_map())
        merged.update(chat_action_command_map())
        return merged
    except Exception:  # pragma: no cover - import cycle safety
        return {}


OVERRIDES: dict[str, tuple[str, str, str]] = {
    census_id: ("GENERATED_ADAPTER", command_id, "legacy surface forwards through execute_command")
    for census_id, command_id in _ADAPTERS.items()
}
OVERRIDES.update(
    {
        census_id: ("EXTERNAL_TRANSPORT_ONLY", "", note)
        for census_id, note in _EXTERNAL.items()
    }
)
OVERRIDES.update(
    {
        census_id: (
            "GENERATED_ADAPTER",
            family[0],
            "legacy surface forwards through execute_command; typed family: "
            + ", ".join(family),
        )
        for census_id, family in _ADAPTER_FAMILIES.items()
    }
)

OVERRIDES.update(
    {
        census_id: (
            "GENERATED_ADAPTER",
            command_id,
            "delegated route forwards through execute_command",
        )
        for census_id, command_id in _delegated_route_adapters().items()
    }
)
