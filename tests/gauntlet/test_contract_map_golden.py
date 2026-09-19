"""Gauntlet — category 14 + 5: runtime tool-contract tier golden lock.

Every runtime tool declares a ``side_effect_class`` (what it can touch) and an
``approval_requirement`` (how hard it is gated). These are the security tiers the
whole tool loop leans on. A silent downgrade — e.g. ``machine.move_path`` losing
``explicit_user_opt_in``, or a new mutating tool shipped as ``read_only`` — would
weaken the sandbox without any test noticing.

This golden freezes the full (intent -> side_effect_class, approval_requirement)
table. Adding a tool, removing one, or changing any tier fails here and forces a
deliberate update with review, instead of landing unremarked.

The tiers are literals in the dataclass construction, so this table is stable
regardless of policy flags (which only flip the separate ``supported`` field).
"""
from __future__ import annotations

import pytest

from core.runtime_tool_contracts import runtime_tool_contract_map, runtime_tool_contracts

pytestmark = [pytest.mark.gauntlet, pytest.mark.safety]


# (intent -> (side_effect_class, approval_requirement)) — captured 2026-07 from the
# live contract set. Update deliberately (with review) when the tool surface changes.
GOLDEN_TIERS: dict[str, tuple[str, str]] = {
    # RECONCILED 2026-09-17 (wallet/contacts lane): the integrated list carried 146 contracts
    # while this golden froze 139 — repo.issue, repo.issue.comments and code.task.pr_description
    # had landed without a refresh (the base fails this file with the same 'added' list), and
    # this lane adds the five contacts.* contracts deliberately: search/resolve are read_only;
    # save/update/delete are creative_state behind runtime_policy (what may be written is
    # decided by request provenance and the operator credential, never by the model alone).
    'browser.render': ('read_only', 'none'),
    'capability.expand_family': ('read_only', 'none'),
    'code.review_evidence': ('read_only', 'none'),
    'code.task.approve': ('read_only', 'none'),
    'code.task.cancel': ('read_only', 'none'),
    'code.task.identify': ('read_only', 'none'),
    'code.task.open': ('read_only', 'none'),
    'code.task.pr_description': ('read_only', 'none'),
    'code.task.propose': ('read_only', 'none'),
    'code.task.report': ('read_only', 'none'),
    'code.task.rollback': ('workspace_write', 'runtime_policy'),
    'code.task.step': ('workspace_write', 'runtime_policy'),
    'contacts.delete': ('creative_state', 'runtime_policy'),
    'contacts.resolve': ('read_only', 'none'),
    'contacts.save': ('creative_state', 'runtime_policy'),
    'contacts.search': ('read_only', 'none'),
    'contacts.update': ('creative_state', 'runtime_policy'),
    'demo.plan': ('read_only', 'none'),
    'email.read': ('read_only', 'none'),
    # Email P0 workflow (mission 01-email, reviewed 2026-09-13): open/search/reconcile
    # read an account (same tier as email.read); draft save/get/approve are the durable
    # REVIEW RECORD under the runtime home — no file, network or send effect, and they
    # carry no permission action (the send is gated separately, below); the draft send
    # is an external message with the same explicit opt-in as email.send/email.reply
    # (plus the content-hash approval binding owned by core.email_drafts).
    'email.draft.approve': ('read_only', 'none'),
    'email.draft.get': ('read_only', 'none'),
    'email.draft.save': ('read_only', 'none'),
    'email.draft.send': ('network_send', 'explicit_user_opt_in'),
    'email.open': ('read_only', 'none'),
    'email.draft.reconcile': ('read_only', 'none'),
    'email.reply': ('network_send', 'explicit_user_opt_in'),
    'email.send': ('network_send', 'explicit_user_opt_in'),
    'image.generate': ('media_generation', 'runtime_policy'),
    'machine.disk_usage': ('read_only', 'none'),
    'machine.display_inspect': ('read_only', 'none'),
    'machine.ensure_directory': ('workspace_write', 'runtime_policy'),
    'machine.event_log_errors': ('read_only', 'none'),
    'machine.find_file': ('read_only', 'none'),
    'machine.find_folder': ('read_only', 'none'),
    'machine.find_largest': ('read_only', 'none'),
    'machine.host_state': ('read_only', 'none'),
    'machine.inspect_specs': ('read_only', 'none'),
    'machine.list_directory': ('read_only', 'none'),
    'machine.list_processes': ('read_only', 'none'),
    'machine.move_path': ('workspace_write', 'explicit_user_opt_in'),
    'machine.read_file': ('read_only', 'none'),
    'machine.write_file': ('workspace_write', 'runtime_policy'),
    'marketplace.purchase_knowledge': ('credit_spend', 'explicit_user_opt_in'),
    'marketplace.search_listings': ('read_only', 'none'),
    'media.edit': ('workspace_write', 'runtime_policy'),
    'media.export': ('workspace_write', 'explicit_user_opt_in'),
    'media.inspect': ('read_only', 'none'),
    'media.open': ('workspace_write', 'runtime_policy'),
    'media.redo': ('workspace_write', 'runtime_policy'),
    'media.undo': ('workspace_write', 'runtime_policy'),
    'operator.apple_note_list': ('read_only', 'none'),
    'operator.apple_note_read': ('read_only', 'none'),
    'operator.check_availability': ('read_only', 'none'),
    'operator.cleanup_temp_files': ('workspace_write', 'explicit_user_opt_in'),
    'operator.find_notes': ('read_only', 'none'),
    'operator.inspect_calendar_event': ('read_only', 'none'),
    'operator.inspect_disk_usage': ('read_only', 'none'),
    'operator.inspect_processes': ('read_only', 'none'),
    'operator.inspect_services': ('read_only', 'none'),
    'operator.list_calendars': ('read_only', 'none'),
    'operator.list_reminders': ('read_only', 'none'),
    'operator.list_tools': ('read_only', 'none'),
    'operator.move_path': ('workspace_write', 'explicit_user_opt_in'),
    'operator.propose_calendar_event': ('network_publish', 'explicit_user_opt_in'),
    'operator.save_note': ('workspace_write', 'explicit_user_opt_in'),
    'operator.schedule_calendar_event': ('workspace_write', 'explicit_user_opt_in'),
    'operator.search_calendar_event': ('read_only', 'none'),
    'operator.show_agenda': ('read_only', 'none'),
    'operator.show_note': ('read_only', 'none'),
    'operator.update_calendar_event': ('network_publish', 'explicit_user_opt_in'),
    'orchestration.execute_envelope': ('task_orchestration', 'runtime_policy'),
    'pay.x402': ('wallet_spend', 'explicit_user_opt_in'),
    'pdf.extract_text': ('read_only', 'none'),
    'pdf.ocr': ('read_only', 'none'),
    'profile.forget': ('creative_state', 'none'),
    'profile.list': ('read_only', 'none'),
    'profile.remember': ('creative_state', 'none'),
    'repo.bind': ('read_only', 'none'),
    'repo.ci.artifacts': ('read_only', 'none'),
    'repo.ci.cancel': ('network_send', 'explicit_user_opt_in'),
    'repo.ci.jobs': ('read_only', 'none'),
    'repo.ci.log': ('read_only', 'none'),
    'repo.ci.rerun': ('network_send', 'explicit_user_opt_in'),
    'repo.diagnose': ('read_only', 'none'),
    'repo.diff': ('read_only', 'none'),
    'repo.git': ('workspace_write', 'explicit_user_opt_in'),
    'repo.inspect': ('read_only', 'none'),
    # 138 -> 145 (2026-09-13, github lane): the pri mission's issue-inspection reads
    # (repo.issue, repo.issue.comments) had never been added here -- this golden ran red on the
    # clean base for exactly that omission, which the tier lock is built to force. This change
    # adds them AND the five forge-action contracts: pr.request is a read (it builds and hashes
    # the exact action text and sends nothing); pr.authorize mints durable consent and is gated
    # like repo.push.authorize; pr.create/pr.update/pr.comment put content on a forge and carry
    # the highest gate, with the operator-gesture law enforced in the RepoOps plane.
    'repo.issue': ('read_only', 'none'),
    'repo.issue.comments': ('read_only', 'none'),
    'repo.pr.authorize': ('runtime_capability_change', 'explicit_user_opt_in'),
    'repo.pr.comment': ('network_send', 'explicit_user_opt_in'),
    'repo.pr.create': ('network_send', 'explicit_user_opt_in'),
    'repo.pr.request': ('read_only', 'none'),
    'repo.pr.update': ('network_send', 'explicit_user_opt_in'),
    'repo.push': ('network_publish', 'explicit_user_opt_in'),
    'repo.push.authorize': ('runtime_capability_change', 'explicit_user_opt_in'),
    'repo.push.request': ('read_only', 'none'),
    'repo.receipt': ('read_only', 'none'),
    'repo.review': ('read_only', 'none'),
    'repo.session.open': ('read_only', 'none'),
    'repo.step': ('workspace_write', 'runtime_policy'),
    'repo.verify_remote': ('read_only', 'none'),
    'respond.direct': ('read_only', 'none'),
    'sandbox.run_command': ('sandbox_command', 'runtime_policy'),
    'sell.quote': ('read_only', 'none'),
    'set.save': ('creative_state', 'none'),
    'set.use': ('read_only', 'none'),
    'skill.create': ('workspace_write', 'runtime_policy'),
    'skill.inspect': ('read_only', 'none'),
    'skill.install': ('runtime_capability_change', 'explicit_user_opt_in'),
    'skill.list': ('read_only', 'none'),
    'skill.rollback': ('runtime_capability_change', 'explicit_user_opt_in'),
    'skill.validate': ('read_only', 'none'),
    'video.generate': ('media_generation', 'runtime_policy'),
    'vool-browser.assert': ('read_only', 'none'),
    'vool-browser.cancel': ('workspace_write', 'runtime_policy'),
    'vool-browser.checkout.begin': ('workspace_write', 'explicit_user_opt_in'),
    'vool-browser.checkout.handoff': ('workspace_write', 'explicit_user_opt_in'),
    'vool-browser.click': ('read_only', 'none'),
    'vool-browser.download': ('workspace_write', 'runtime_policy'),
    'vool-browser.inspect': ('read_only', 'none'),
    'vool-browser.navigate': ('read_only', 'none'),
    'vool-browser.offer.compare': ('read_only', 'none'),
    'vool-browser.offer.extract': ('read_only', 'none'),
    'vool-browser.order.reconcile': ('workspace_write', 'explicit_user_opt_in'),
    'vool-browser.permission.grant': ('read_only', 'none'),
    'vool-browser.permission.list': ('read_only', 'none'),
    'vool-browser.profile.confirm': ('workspace_write', 'runtime_policy'),
    'vool-browser.screenshot': ('workspace_write', 'runtime_policy'),
    'vool-browser.session.close': ('workspace_write', 'runtime_policy'),
    'vool-browser.session.open': ('workspace_write', 'runtime_policy'),
    'vool-browser.session.status': ('read_only', 'none'),
    'vool-browser.type': ('read_only', 'none'),
    'vool-browser.upload': ('workspace_write', 'runtime_policy'),
    'vool-browser.upload.stage': ('workspace_write', 'runtime_policy'),
    'wallet.payment_status': ('read_only', 'none'),
    'wallet.propose': ('creative_state', 'none'),
    'wallet.simulate': ('read_only', 'none'),
    'wallet.spend': ('wallet_spend', 'explicit_user_opt_in'),
    'wallet.status': ('read_only', 'none'),
    'web.fetch': ('read_only', 'none'),
    'web.research': ('read_only', 'none'),
    'web.search': ('read_only', 'none'),
    'web0.add_block': ('builder_state', 'none'),
    'web0.add_gated_section': ('builder_state', 'none'),
    'web0.compile_preview': ('builder_state', 'none'),
    'web0.create_project': ('builder_state', 'none'),
    'web0.encrypt_whole_site': ('builder_state', 'none'),
    'web0.fill_slots': ('builder_state', 'none'),
    'web0.open_builder_draft': ('builder_state', 'none'),
    'web0.publish': ('network_publish', 'explicit_user_opt_in'),
    'workspace.apply_unified_diff': ('workspace_write', 'runtime_policy'),
    'workspace.ensure_directory': ('workspace_write', 'runtime_policy'),
    'workspace.git_diff': ('read_only', 'none'),
    'workspace.git_status': ('read_only', 'none'),
    'workspace.git_summary': ('read_only', 'none'),
    'workspace.identity': ('read_only', 'none'),
    'workspace.list_files': ('read_only', 'none'),
    'workspace.list_tree': ('read_only', 'none'),
    'workspace.read_file': ('read_only', 'none'),
    'workspace.replace_in_file': ('workspace_write', 'runtime_policy'),
    'workspace.rollback_last_change': ('workspace_write', 'runtime_policy'),
    'workspace.run_formatter': ('validation_command', 'runtime_policy'),
    'workspace.run_lint': ('validation_command', 'runtime_policy'),
    'workspace.run_tests': ('validation_command', 'runtime_policy'),
    'workspace.search_text': ('read_only', 'none'),
    'workspace.symbol_search': ('read_only', 'none'),
    'workspace.write_file': ('workspace_write', 'runtime_policy'),
    'x.trending': ('read_only', 'none'),
    'x402.propose': ('creative_state', 'none'),
}

# The tools allowed to move real bytes / bits outside the sandbox at the highest
# gate. If anything else ever earns explicit_user_opt_in, that is a deliberate call.
EXPECTED_EXPLICIT_OPT_IN = {
    # RepoOps remote truth (cancel/rerun/git-mutation/push) and media export leave the
    # machine or move real state — they hold the highest gate by design.
    # email.draft.send sends the reviewed draft: same external-message gate as
    # email.send/email.reply (plus the draft-state approval binding in core.email_drafts).
    "email.draft.send",
    "email.reply",
    "email.send",
    "machine.move_path",
    "marketplace.purchase_knowledge",
    "media.export",
    "operator.cleanup_temp_files",
    "operator.move_path",
    "operator.schedule_calendar_event",
    # The operator calendar/notes vertical (2026-09-15): writing a note and mutating the
    # operator's own calendar are opt-in acts, and proposing/updating an event sends
    # invitations to other people -- the same external-message gate as email.
    "operator.propose_calendar_event",
    "operator.save_note",
    "operator.update_calendar_event",
    "pay.x402",
    # C07 checkout handoff (canonical convergence checkpoint 8). These three are the only
    # browser intents at the highest gate, and they must stay there: two begin/hand off a
    # real checkout to the operator's native wallet, and the third reconciles a merchant
    # order. VOOL never clicks Pay and never takes card data — the gate is what keeps the
    # foreground payment act with the operator.
    "vool-browser.checkout.begin",
    "vool-browser.checkout.handoff",
    "vool-browser.order.reconcile",
    "repo.ci.cancel",
    "repo.ci.rerun",
    "repo.git",
    "repo.pr.authorize",
    "repo.pr.comment",
    "repo.pr.create",
    "repo.pr.update",
    "repo.push",
    "repo.push.authorize",
    "skill.install",
    "skill.rollback",
    "wallet.spend",
    "web0.publish",
}

# Side-effect classes that must never be reachable without at least a policy gate.
_MUTATING_CLASSES = {
    "workspace_write",
    # Added with skill.install. A class absent from this set is a class the ungated-mutation check
    # cannot see, so a new side-effect class must be listed here or it is exempt by accident.
    "runtime_capability_change",
    "sandbox_command",
    "network_publish",
    "network_send",
    "media_generation",
    "task_orchestration",
    "validation_command",
    "wallet_spend",
}


def _current_tiers() -> dict[str, tuple[str, str]]:
    return {c.intent: (c.side_effect_class, c.approval_requirement) for c in runtime_tool_contracts()}


def test_contract_tier_table_matches_golden():
    current = _current_tiers()

    added = sorted(set(current) - set(GOLDEN_TIERS))
    removed = sorted(set(GOLDEN_TIERS) - set(current))
    changed = sorted(
        f"{intent}: golden {GOLDEN_TIERS[intent]} -> now {current[intent]}"
        for intent in set(current) & set(GOLDEN_TIERS)
        if current[intent] != GOLDEN_TIERS[intent]
    )

    assert not added, f"New runtime tool(s) not in the tier golden — add them deliberately: {added}"
    assert not removed, f"Runtime tool(s) removed from the golden: {removed}"
    assert not changed, f"Tool security tier changed — review before updating the golden: {changed}"


def test_contract_map_and_list_agree():
    contract_map = runtime_tool_contract_map()
    assert set(contract_map) == set(GOLDEN_TIERS)
    # 53 -> 58: the pdf.* (2) and skill.* (3) families, each pinned in GOLDEN_TIERS above.
    # 58 -> 59: machine.host_state (uptime / battery / chassis), read-only.
    # 59 -> 60: skill.list, read-only -- the live product refused "what exact skills do we have in
    # our workspace folder?" with "I have no tool for listing them" while the workspace held ten
    # SKILL.md files; the tool it claimed not to have now exists.
    # 60 -> 72: the tooling audit contracted 12 of the 13 intents that `runtime_tool_specs()` advertised
    # to every model with NO contract at all, so their gating fell through to intent-string
    # matching. Three of them (operator.cleanup_temp_files / move_path / schedule_calendar_event)
    # delete, move and write on the operator's machine. No capability was added or removed and no
    # permission was moved -- each contract declares the gate the fallback was already deriving.
    # 73 -> 74: skill.rollback. The versioned skill lifecycle records every activation;
    # restoring a prior version is itself an activation-class change, gated like skill.install
    # (explicit_user_opt_in), so an edit that made a skill worse is recoverable in product.
    # 72 -> 73: machine.find_file. There was no tool that could find a file by name outside the
    # bound workspace -- find_folder matches directories, list_files is workspace-confined -- so
    # "find vool_agent.py on my Desktop" had no lane and the runtime answered that the file did
    # not exist. Read-only, same safe roots and ceilings as find_folder.
    # 73 -> 112: the 2026-09-04 reconciliation (see the note on GOLDEN_TIERS) — the lock now
    # covers every integrated contract again; skill.rollback takes it from 111 to 112.
    # 112 -> 133: canonical convergence checkpoint 8 lands the vool-browser lane's 21 contracts
    # (C06 navigation/inspection + C07 shopping and checkout handoff).
    # 133 -> 138: convergence checkpoint 11 lands the wallet/x402 lane's five contracts
    # (three reads, two proposal drafts; the spending intents were already pinned).
    # 138 -> 146: the github lane (2026-09-13) -- two issue-inspection reads that should have
    # been pinned when they landed, code.task.pr_description (same omission, same lane family),
    # and the five forge-action contracts (see the note at the repo.* entries above).
    # 146 -> 159: the operator calendar/notes vertical declared 2026-09-15 (ten reads, two
    # writes, and the two invitation-sending calendar mutations at network_publish) plus the
    # code.task/contacts/profile/repo/skill contracts that landed unpinned with their lanes.
    assert len(contract_map) == len(GOLDEN_TIERS) == 170


def test_only_expected_tools_hold_explicit_opt_in():
    current = _current_tiers()
    explicit = {intent for intent, (_sec, appr) in current.items() if appr == "explicit_user_opt_in"}
    assert explicit == EXPECTED_EXPLICIT_OPT_IN


def test_no_mutating_tool_is_ungated():
    """Every tool that can write/execute/publish/spend must carry a non-'none'
    approval requirement, OR be an explicitly-reviewed exception. This catches a
    new mutating tool shipped without a gate."""
    current = _current_tiers()
    ungated_mutations = sorted(
        intent
        for intent, (sec, appr) in current.items()
        if sec in _MUTATING_CLASSES and appr == "none"
    )
    assert not ungated_mutations, (
        f"Mutating tool(s) with no approval gate: {ungated_mutations}"
    )


def test_credit_spend_tool_is_accounted_for():
    """marketplace.purchase_knowledge spends credits at approval 'none' — it is a
    known, buyer-pinned local-credit action. Pin it so a change is deliberate."""
    current = _current_tiers()
    credit_tools = {intent for intent, (sec, _appr) in current.items() if sec == "credit_spend"}
    assert credit_tools == {"marketplace.purchase_knowledge"}
