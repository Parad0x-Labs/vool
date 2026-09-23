"""Companion presentation fragment: the VOOL Ninja + typed Vooling status layer.

Concatenated onto the served /chat document by ``render_vool_chat_html`` (see the
one-line concatenation in ``core/vool_chat_page.py``). Everything in this module is
PRESENTATION: it consumes the existing typed ``vool_event`` channel through the single
``VoolCompanion.consume`` call made from ``applyTaskEvent`` and never creates, infers or
advances execution truth.

Laws baked in (demo-companion-vooling-ux pass-001, docs 02/03/04/05/10):

* One source of truth: text phrase, sprite pose and indicator tone all derive from the
  same reducer output over the same typed events. No second state machine.
* No timer-driven progression: wall-clock drives animation cadence and the paint
  scheduler only. A phrase changes only when a typed event changes the reducer state;
  the periodic re-assert tick can re-paint current truth but can never derive new truth.
* FINISH ("Assembling the answer…") is unreachable before the server's real
  ``task.finalizing`` event (producer S1, emitted in the commit-assembly window).
* SUCCESS green only after a real PASS; failure never renders green; UNKNOWN stays a
  distinct muted amber-grey; colour is never the sole carrier (the word is always shown).
* Exactly ONE companion sprite: it presents the one served worker that exists today.
  There is no code path that can spawn a second sprite, so a fake fleet is impossible
  by construction; multi-worker rendering waits for real AGENT_* producers.
* No chain-of-thought: the channel is allowlist-only upstream; the popover renders only
  typed fields (model provenance tiers, lane, measurable progress, review verdict).
* Drag/dock/hide changes presentation geometry only, persisted client-side under
  ``vool_ninja_pos_v1``; nothing here touches run/event/store objects.

The approved 48x48 BigHead-family pixel world is shared with the native desktop companion.
Typed state colour remains a separate indicator/caption truth; clothing never impersonates
PASS, FAILED, UNKNOWN, or approval state.
"""

VN_POS_KEY = "vool_ninja_pos_v1"

# --------------------------------------------------------------------------- CSS
_VN_CSS = r"""
.vn-caption{font-size:9px;font-family:ui-monospace,Menlo,monospace;background:rgba(22,25,31,.92);border:1px solid #2b3140;border-radius:6px;padding:1px 7px;color:var(--vn-tone,#9aa1af);white-space:nowrap;margin-top:2px;letter-spacing:.06em;max-width:100%;overflow:hidden;text-overflow:ellipsis;opacity:0;pointer-events:none;transition:opacity .16s ease}
#companionLayer .vn-caption.vn-show,#companionLayer .vool-ninja:hover .vn-caption,#companionLayer .vool-ninja:focus-visible .vn-caption{opacity:1}
body.vn-motion-reduced #companionLayer .vn-caption{transition:none}
.vn-lab-matrix{display:flex;flex-wrap:wrap;gap:8px;margin:6px 0 10px}
.vn-lab-cell{display:flex;flex-direction:column;align-items:center;gap:2px}
.vn-lab-cell canvas{width:64px;height:64px;image-rendering:pixelated;background:#101216;border:1px solid #262b35;border-radius:8px}
.vn-lab-cell small{font-size:9px;color:#9aa1af;font-family:ui-monospace,Menlo,monospace}
/* vool-ninja companion layer — presentation only, appended by companion_presentation_fragment */
#companionLayer { position: fixed; inset: 0; pointer-events: none; z-index: 30; --vn-tone: #94a3b8; }
#companionLayer .vool-ninja {
  position: fixed; left: 0; top: 0; width: var(--vn-size, 112px); height: var(--vn-size, 112px);
  pointer-events: auto; cursor: grab; touch-action: none; user-select: none;
  border-radius: 10px; display: flex; flex-direction:column; align-items: center; justify-content: center; gap: 2px;
  background: transparent; -webkit-tap-highlight-color: transparent;
}
#companionLayer .vool-ninja:focus { outline: none; }
#companionLayer .vn-caption:empty, #companionLayer .vn-menu:empty { display: none; }
#companionLayer .vool-ninja:focus-visible { outline: 2px solid var(--vn-tone, #94a3b8); outline-offset: 2px; }
#companionLayer .vool-ninja.dragging { cursor: grabbing; opacity: .92; }
#companionLayer .vool-ninja.over-critical { opacity: .6; }
/* P2 layout law: the canvas owns a FIXED basis and the caption owns its own row beneath it.
   The old row-flex with a 100%-width canvas squeezed the caption text against the sprite box. */
#companionLayer .vool-ninja canvas { width: 56px; height: 56px; flex: 0 0 auto; image-rendering: pixelated; }
#companionLayer .vool-ninja.chip { --vn-size: 72px; border-radius: 8px; }
#companionLayer .vool-ninja.chip canvas { width: 44px; height: 44px; }
/* No status dot on the sprite (product/desktop-usability-20260917): it sat at the CORNER of the
   112px drag box, ~1cm from the character's drawn feet, and read as a stray floating mark. It
   duplicated the tone, and the pack's colour law already requires the word to carry every
   state -- colour is never the sole carrier -- so removing it loses no information. The restore
   chip keeps its own small dot below: that one is part of a real control. */
#companionLayer .vn-sr {
  position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap;
}
#companionLayer .vn-restore {
  position: fixed; left: 10px; top: 10px; pointer-events: auto; display: none;
  background: var(--panel, #14181f); color: var(--ink, #e6ebf2); border: 1px solid var(--border, #2a3140);
  border-radius: 8px; font-size: 11px; padding: 4px 8px; cursor: pointer;
}
#companionLayer .vn-restore:focus-visible { outline: 2px solid var(--vn-tone, #94a3b8); }
#companionLayer .vn-restore .vn-dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%;
  background: var(--vn-tone, #94a3b8); margin-right: 5px; vertical-align: 1px; }
body.vn-motion-reduced #companionLayer .vool-ninja { transition: none !important; }
.vool-ninja-pop {
  position: fixed; z-index: 45; width: 300px; max-height: min(70vh, 480px); overflow-y: auto;
  background: #0b0f14; color: var(--ink, #e6ebf2); border: 1px solid var(--border, #2a3140);
  border-radius: 10px; box-shadow: 0 10px 30px rgba(0,0,0,.5); padding: 10px 12px; font-size: 12px;
}
.vool-ninja-pop h4 { margin: 8px 0 3px; font-size: 11px; text-transform: uppercase; letter-spacing: .06em; color: var(--muted, #8b94a3); }
.vool-ninja-pop h4:first-child { margin-top: 0; }
.vool-ninja-pop .vn-row { display: flex; justify-content: space-between; gap: 8px; padding: 1px 0; }
.vool-ninja-pop .vn-row b { font-weight: 600; }
.vool-ninja-pop .vn-row span { color: var(--muted, #8b94a3); text-align: right; word-break: break-word; }
.vool-ninja-pop .vn-verdict { font-weight: 700; }
.vool-ninja-pop .vn-close { position: absolute; right: 8px; top: 6px; background: none; border: none;
  color: var(--muted, #8b94a3); cursor: pointer; font-size: 14px; }
.vool-ninja-menu {
  position: fixed; z-index: 46; min-width: 180px; background: var(--panel, #14181f);
  color: var(--ink, #e6ebf2); border: 1px solid var(--border, #2a3140); border-radius: 10px;
  box-shadow: 0 8px 26px rgba(0,0,0,.5); padding: 4px; font-size: 12px;
}
.vool-ninja-menu button { display: block; width: 100%; text-align: left; background: none; border: none;
  color: inherit; padding: 6px 9px; border-radius: 7px; cursor: pointer; font-size: 12px; }
.vool-ninja-menu button:hover { background: rgba(255,255,255,.06); }
.vool-ninja-menu button:focus-visible { outline: 2px solid var(--vn-tone, #94a3b8); }
.vool-character-lab {
  position: fixed; inset: 0; z-index: 47; display: none; align-items: flex-start; justify-content: center;
  pointer-events: auto; background: rgba(5,7,10,.68); padding: min(11vh, 84px) 16px 24px;
}
.vool-character-lab .vn-lab-modal {
  width: min(620px, calc(100vw - 32px)); max-height: min(76vh, 620px); overflow: auto;
  background: #11151b; color: var(--ink, #e6ebf2); border: 1px solid var(--border, #2a3140);
  border-radius: 14px; box-shadow: 0 18px 55px rgba(0,0,0,.62); padding: 16px;
}
.vool-character-lab .vn-lab-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.vool-character-lab h3 { margin: 0; font-size: 15px; }
.vool-character-lab .vn-lab-sub { margin: 4px 0 14px; color: var(--muted, #8b94a3); font-size: 11px; }
.vool-character-lab .vn-lab-close { border: 0; background: none; color: var(--muted, #8b94a3); cursor: pointer; font-size: 18px; }
.vool-character-lab .vn-lab-grid { display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); gap: 8px; }
.vool-character-lab .vn-character-card {
  color: inherit; background: #161b22; border: 1px solid #2a3140; border-radius: 10px; padding: 8px;
  cursor: pointer; text-align: left; min-width: 0;
}
.vool-character-lab .vn-character-card.selected { border-color: #5eead4; box-shadow: inset 0 0 0 1px #5eead4; }
.vool-character-lab .vn-character-card canvas { display: block; width: 96px; height: 96px; max-width: 100%; margin: auto; image-rendering: pixelated; }
.vool-character-lab .vn-character-card b { display: block; margin-top: 5px; font-size: 11px; }
.vool-character-lab .vn-character-card small { display: block; color: var(--muted, #8b94a3); font-size: 9px; }
.vool-character-lab .vn-pack-title { margin: 15px 0 7px; color: var(--muted, #8b94a3); font-size: 10px; text-transform: uppercase; letter-spacing: .08em; }
.vool-character-lab .vn-pack-grid { display: grid; grid-template-columns: repeat(4,minmax(0,1fr)); gap: 6px; }
.vool-character-lab .vn-pack-card { border: 1px solid #2a3140; border-radius: 8px; background: #161b22; color: inherit; padding: 7px; cursor: pointer; font-size: 10px; }
.vool-character-lab .vn-pack-card.selected { border-color: #5eead4; color: #a7f3e5; }
.vool-character-lab button:focus-visible { outline: 2px solid var(--vn-tone, #94a3b8); outline-offset: 2px; }
@media (max-width: 520px) {
  .vool-character-lab .vn-lab-grid { grid-template-columns: 1fr; }
  .vool-character-lab .vn-character-card { display: grid; grid-template-columns: 80px 1fr; align-items: center; }
  .vool-character-lab .vn-character-card canvas { width: 76px; height: 76px; grid-row: span 2; }
  .vool-character-lab .vn-pack-grid { grid-template-columns: repeat(2,minmax(0,1fr)); }
}
@media (prefers-reduced-motion: reduce) {
  #companionLayer .vool-ninja { transition: none !important; }
}
/* The companion repaints the displayed card's stage line with the typed phrase + tone. A short
   colour transition makes a typed state change visibly travel (screen-recordable) without any
   timer deriving state: the text still changes only when a typed event changes the reducer. */
.task-card .tc-stage { transition: color .35s ease; }
body.vn-motion-reduced .task-card .tc-stage { transition: none !important; }
@media (prefers-reduced-motion: reduce) { .task-card .tc-stage { transition: none !important; } }"""

# ------------------------------------------------------------------- sprite data

# ------------------------------------------------------- phrase library + reducer
_VN_LIBRARY_JS = r"""// Typed phrase library (demo-companion-vooling-ux pass-001 doc 03 §4, verbatim).
// Every rotating phrase belongs to exactly ONE activity category; every rotating text
// ends with an ellipsis. Fixed-state copy lives in VN_FIXED and never rotates.
const VN_LIBRARY = {
  UNDERSTANDING: [
    ["vooling", "Vooling…"], ["burrowing_context", "Burrowing into context…"],
    ["untangling_task", "Untangling the task…"], ["reading_brief", "Reading the brief…"],
    ["scanning_conversation", "Scanning the conversation…"], ["loading_prior_context", "Loading prior context…"],
    ["getting_oriented", "Getting oriented…"], ["sizing_request", "Sizing up the request…"],
    ["finding_thread", "Finding the thread…"], ["checking_ground", "Checking the ground…"]],
  PLANNING: [
    ["mapping_work", "Mapping the work…"], ["laying_steps", "Laying out the steps…"],
    ["sequencing_moves", "Sequencing the moves…"], ["drawing_route", "Drawing the route…"],
    ["breaking_down", "Breaking down the task…"], ["choosing_lane", "Choosing the lane…"]],
  WEB_RESEARCH: [
    ["searching_web", "Searching the web…"], ["running_across_web", "Running across the web…"],
    ["gathering_sources", "Gathering sources…"], ["following_references", "Following references…"],
    ["reading_primary_sources", "Reading primary sources…"], ["crosschecking_claims", "Cross-checking claims…"],
    ["comparing_reports", "Comparing reports…"], ["harvesting_evidence", "Harvesting useful evidence…"],
    ["checking_latest", "Checking the latest information…"], ["tracing_source_trail", "Tracing the source trail…"]],
  READING_FILES: [
    ["reading_working_tree", "Reading the working tree…"], ["opening_referenced_files", "Opening referenced files…"],
    ["scanning_related_modules", "Scanning related modules…"], ["walking_dependency_graph", "Walking the dependency graph…"],
    ["checking_stored_context", "Checking stored context…"], ["collecting_disk_facts", "Collecting facts on disk…"],
    ["following_reference_trail", "Following the trail of references…"]],
  APPLYING_CHANGES: [
    ["applying_edits", "Applying edits…"], ["writing_changes_into_place", "Writing changes into place…"],
    ["patching_work", "Patching the work…"], ["shaping_artifacts", "Shaping the artifacts…"],
    ["laying_down_changes", "Laying down changes…"]],
  RUNNING_TOOLS: [
    ["calling_required_tools", "Calling the required tools…"], ["gathering_tool_results", "Gathering tool results…"],
    ["waiting_on_execution", "Waiting on execution…"], ["reconciling_tool_output", "Reconciling tool output…"],
    ["following_process_tree", "Following the process tree…"], ["checking_runtime_health", "Checking runtime health…"],
    ["inspecting_active_processes", "Inspecting active processes…"], ["tracing_execution_paths", "Tracing execution paths…"],
    ["running_the_build", "Running the build…"], ["watching_process_land", "Watching the process run end-to-end…"]],
  TESTING_CI: [
    ["sweeping_test_suite", "Sweeping the test suite…"], ["sweeping_ci_workflows", "Sweeping CI workflows…"],
    ["checking_shell_gates", "Checking shell gates…"], ["replaying_failing_tests", "Replaying failing tests…"],
    ["checking_test_isolation", "Checking test isolation…"], ["hunting_order_pollution", "Hunting order pollution…"],
    ["verifying_mutation_proof", "Verifying mutation proof…"], ["exercising_served_path", "Exercising the served path…"],
    ["checking_masked_failures", "Checking for masked failures…"], ["stressing_release_gates", "Stressing the release gates…"]],
  GIT_INSPECTION: [
    ["walking_commit_history", "Walking the commit history…"], ["comparing_branches", "Comparing branches…"],
    ["checking_worktree_state", "Checking worktree state…"], ["tracing_commit_chain", "Tracing the commit chain…"],
    ["comparing_exact_shas", "Comparing exact SHAs…"], ["inspecting_diff", "Inspecting the diff…"],
    ["inspecting_local_changes", "Inspecting local changes…"], ["checking_branch_divergence", "Checking branch divergence…"],
    ["following_sha_trail", "Following the SHA trail…"], ["mapping_repository_state", "Mapping repository state…"],
    ["looking_uncommitted_work", "Looking for uncommitted work…"], ["verifying_exact_candidate", "Verifying the exact candidate…"]],
  VERIFYING: [
    ["testing_counterexample", "Testing the counterexample…"], ["crosschecking_evidence", "Cross-checking evidence…"],
    ["verifying_actually_ran", "Verifying what actually ran…"], ["reconciling_runtime_state", "Reconciling runtime state…"],
    ["hunting_false_greens", "Hunting false greens…"], ["stressing_assumptions", "Stressing the assumptions…"],
    ["closing_loose_ends", "Closing loose ends…"], ["untangling_competing_paths", "Untangling competing paths…"],
    ["looking_stale_state", "Looking for stale state…"], ["replaying_failure_path", "Replaying the failure path…"],
    ["checking_served_boundary", "Checking the served boundary…"], ["testing_real_route", "Testing the real route…"]],
  ROUTING_PROVENANCE: [
    ["routing_between_lanes", "Routing between lanes…"], ["following_model_provenance", "Following model provenance…"],
    ["inspecting_provider_state", "Inspecting provider state…"], ["mapping_authority_path", "Mapping the authority path…"],
    ["comparing_candidate_models", "Comparing candidate models…"], ["checking_locality", "Checking what stayed local…"]],
  SYNTHESIZING: [
    ["threading_findings", "Threading the findings…"], ["condensing_evidence", "Condensing the evidence…"],
    ["connecting_evidence", "Connecting the evidence…"], ["connecting_dots", "Connecting the dots…"],
    ["weaving_together", "Weaving it together…"], ["working_pieces", "Working through the pieces…"],
    ["drawing_throughline", "Drawing the through-line…"], ["distilling_matters", "Distilling what matters…"]],
  FINALIZING: [
    ["assembling_answer", "Assembling the answer…"], ["assembling_result", "Assembling the result…"],
    ["final_touches", "Applying final touches…"], ["preparing_final_output", "Preparing final output…"],
    ["polishing", "Polishing…"], ["finalizing", "Finalizing…"], ["unfurling", "Unfurling…"]],
  MODEL_GENERATION: [
    ["drafting_reply", "Drafting the reply…"], ["composing_text", "Composing…"],
    ["arriving_wording", "Arriving at the wording…"], ["pulling_threads_sentences", "Pulling threads into sentences…"],
    ["saying_plainly", "Saying it plainly…"]],
  WAITING_QUEUED: [
    ["holding_position", "Holding position…"], ["queued_behind_work", "Queued behind current work…"],
    ["standing_by", "Standing by…"], ["warming_up", "Warming up…"]],
  RETRYING: [
    ["retrying_attempt", "Retrying the attempt…"], ["backing_off", "Backing off briefly…"],
    ["another_angle", "Taking another angle…"], ["recovering_stumble", "Recovering from a stumble…"],
    ["rerouting_failure", "Re-routing around the failure…"]],
};

// Fixed-state copy: terminal/attention states never rotate and never sound cute.
const VN_FIXED = {
  APPROVAL_REQUIRED: ["approval_required", "Waiting for your approval…"],
  FAILED: ["failed", "Stopped safely."],
  CANCELLED: ["cancelled", "Cancelled."],
  UNKNOWN: ["unknown", "State unknown."],
  FLAGGED: ["flagged", "Review flagged the result."],
  VERIFIER_INCOMPLETE: ["verifier_incomplete", "No reviewer verdict was established."],
  PASS: ["pass", "Complete."],
  // Ordinary completion WITHOUT a verifier ran: its own truthful terminal (P2). It states the
  // completion and the absence of independent review, in a neutral tone — never PASS, and never
  // the old "State unknown", which read as if something had gone subtly wrong when nothing had.
  COMPLETED_UNREVIEWED: ["completed_unreviewed", "Completed — not independently reviewed."],
};

// Typed colour language (TDL addendum). Tone derives from the SAME reducer output as
// the phrase; colour is never the sole carrier — the word is always rendered alongside.
const VN_TONES = {
  start: "#38bdf8",        // START / voolling — cool cyan, calm exploration
  active: "#22d3ee",       // active execution — brighter electric cyan
  synthesis: "#a78bfa",    // synthesis / threading — violet indigo
  verify: "#fbbf24",       // verification — warm gold attention, NOT failure
  final: "#2dd4bf",        // finalization — teal, only on the real task.finalizing
  waiting: "#94a3b8",      // waiting — neutral grey
  approval: "#fb923c",     // approval required — persistent orange
  retry: "#f97316",        // retry / recovery — controlled orange, not red panic
  success: "#34d399",      // PASS only
  failure: "#f87171",      // failure — red
  unknown: "#b3a284",      // unknown / unverified — muted amber-grey, distinct from pass and fail
  cancelled: "#9ca3af",    // cancelled — neutral grey
  completed: "#7dd3fc",    // completed without independent review — neutral sky, never PASS green
  idle: "#8b94a3",
};
const VN_CATEGORY_TONES = {
  UNDERSTANDING: "start", PLANNING: "start",
  ROUTING_PROVENANCE: "active", WEB_RESEARCH: "active", READING_FILES: "active",
  APPLYING_CHANGES: "active", GIT_INSPECTION: "active", TESTING_CI: "active",
  RUNNING_TOOLS: "active", MODEL_GENERATION: "active",
  SYNTHESIZING: "synthesis", VERIFYING: "verify", WAITING_QUEUED: "waiting", RETRYING: "retry",
  FINALIZING: "final",
};
const VN_OVERRIDE_TONES = {
  APPROVAL_REQUIRED: "approval", FAILED: "failure", CANCELLED: "cancelled",
  UNKNOWN: "unknown", FLAGGED: "verify", VERIFIER_INCOMPLETE: "unknown",
  PASS: "success",
  COMPLETED_UNREVIEWED: "completed",
};

// Deterministic helpers. Math.random and Date.now are banned from selection paths.
function vnFnv1a(str) {
  let h = 0x811c9dc5;
  for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 0x01000193); }
  return h >>> 0;
}

// ---------------------------------------------------------------------------
// Classifier: typed event -> activity category. Registry order mirrors
// _TOOL_STAGE_RULES semantics (ordered needles, first match wins). RETRYING
// requires real retry evidence and can never fire from the stage alone;
// SYNTHESIZING (raw_type tool_synthesizing) beats its Verifying stage;
// FINALIZING requires the server's task.finalizing event — nothing else.
const VN_RETRY_RAW = { tool_repeat_blocked: 1, model_lane_failed: 1, model_lane_contract_failed: 1 };
const VN_TOOL_RULES = [
  [["search", "research", "web", "lookup", "browse", "quote"], "WEB_RESEARCH"],
  [["read", "fetch", "open", "inspect", "view", "cat", "load"], "READING_FILES"],
  [["edit", "write", "patch", "apply", "create_file", "modify", "format"], "APPLYING_CHANGES"],
  [["git_status", "git_diff", "git_log", "git_branch", "worktree", "blame", "show", "commit", "sha"], "GIT_INSPECTION"],
  [["test", "pytest", "verify", "assert", "ci", "workflow", "shell_gate"], "TESTING_CI"],
  [["run", "exec", "start", "server", "preview", "build", "shell", "command", "install"], "RUNNING_TOOLS"],
];
function vnClassify(ev) {
  if (!ev || !ev.type) return null;
  if (ev.type === "task.finalizing") return "FINALIZING";
  if (ev.raw_type === "tool_synthesizing") return "SYNTHESIZING";
  if (ev.stage === "Repairing" && ev.diagnostics && ev.diagnostics.retryable === true) return "RETRYING";
  if (VN_RETRY_RAW[ev.raw_type]) return "RETRYING";
  if (ev.type === "permission.required") return "APPROVAL_REQUIRED";
  if (ev.type === "verification.started" || ev.type === "verification.completed") return "VERIFYING";
  if (ev.type === "task.started" || ev.stage === "Understanding") return "UNDERSTANDING";
  if (ev.type === "model.call_started" || ev.type === "model.call_completed") return "MODEL_GENERATION";
  if (ev.type === "plan.step_started" || ev.type === "plan.step_completed") return "PLANNING";
  if (ev.raw_type === "model_routing_started" || ev.stage === "Planning") return "PLANNING";
  if (/^model\./.test(ev.type) || ev.type === "cloud.cost_updated") return "ROUTING_PROVENANCE";
  // Lane node events are REAL execution rows (one per live-data subtask, emitted around the
  // actual fetch). They reach the client on the same typed channel as tool events, so the
  // classifier reads them through the SAME tool-rule table using the node's declared operation
  // as the tool name. Without this arm a turn that genuinely fetched weather/market data
  // narrated nothing: the categories that describe retrieval never activated.
  if (ev.type === "agent_node_started" || ev.type === "agent_node_completed"
      || ev.raw_type === "agent_node_started" || ev.raw_type === "agent_node_completed") {
    const op = String(ev.operation || ev.tool || "").toLowerCase();
    for (let i = 0; i < VN_TOOL_RULES.length; i++) {
      const needles = VN_TOOL_RULES[i][0];
      for (let j = 0; j < needles.length; j++) if (op.indexOf(needles[j]) !== -1) return VN_TOOL_RULES[i][1];
    }
    return op ? "RUNNING_TOOLS" : null;
  }
  if (ev.type === "tool.started" || ev.type === "tool.completed" || ev.type === "tool.failed") {
    const t = String(ev.tool || "").toLowerCase();
    for (let i = 0; i < VN_TOOL_RULES.length; i++) {
      const needles = VN_TOOL_RULES[i][0];
      for (let j = 0; j < needles.length; j++) if (t.indexOf(needles[j]) !== -1) return VN_TOOL_RULES[i][1];
    }
    return "RUNNING_TOOLS";
  }
  if (ev.type === "task.restored") return "WAITING_QUEUED";
  return null;
}

// ---------------------------------------------------------------------------
// Reducer: a pure typed-event consumer per chat lane (port of the
// CompanionPresentationState semantics from render-ninja/pass1): stale-seq
// rejection, transient failure recovery, approval lock, FINISH gate.
const VN_VIEWS = new Map();
function vnNewView() {
  return {
    seq: -1, events: 0, started: false, middleSeen: false, activeTool: null,
    category: null, catFp: null,
    approvalSeq: null, failureSeq: -1, failureN: -1, retrySeq: -1, finishSeq: null,
    catN: -1,
    terminal: null, reviewState: null,
    model: null, cost: null, measurable: null, activeMode: null, expiresAt: null,
    lastSummary: null, lastTool: null, diagnostics: null,
    phraseId: null, phraseCategory: null, rotation: {},
  };
}
function vnView(chatId) {
  const key = String(chatId || "");
  if (!VN_VIEWS.has(key)) VN_VIEWS.set(key, vnNewView());
  return VN_VIEWS.get(key);
}
function vnApply(view, ev) {
  // Stale/delayed events are rejected exactly like the house consumer rejects them.
  if (ev.seq != null) {
    const seq = Number(ev.seq) || 0;
    if (seq && seq < view.seq) return false;
    if (seq) view.seq = Math.max(view.seq, seq);
  }
  view.events += 1;
  const fp = ev.seq != null ? "s" + ev.seq : "a" + view.events;
  const cat = vnClassify(ev);

  // A new turn on the same lane resets the previous turn's terminal truth. `task.submitted` is
  // the page's own fact (the request left the composer, the server has acknowledged nothing);
  // `task.started` is the server's. Either one opens a new turn; only the second sets `started`.
  if (ev.type === "task.started" || ev.type === "task.submitted") {
    view.terminal = null; view.finishSeq = null; view.approvalSeq = null;
    view.failureSeq = -1; view.failureN = -1; view.retrySeq = -1;
    view.reviewState = null; view.measurable = null; view.diagnostics = null;
    view.middleSeen = false;
    if (ev.type === "task.submitted") {
      // Nothing is known about the new turn yet: no server acknowledgement, no category, no
      // tool. Carrying the previous turn's category here would narrate work not yet started.
      view.started = false; view.category = null; view.catFp = null; view.catN = -1;
      view.activeTool = null; view.lastSummary = null; view.lastTool = null;
    }
  }
  // Terminal truth is immutable: a late DONE can never repaint a FAILED turn
  // (FAILED beats late DONE). The house consumer enforces the same rule upstream.
  if (view.terminal && (ev.type === "task.completed" || ev.type === "task.failed" ||
      ev.type === "task.cancelled" || ev.type === "task.finalizing")) {
    return true;
  }

  // Typed field capture for the identity popover (the popover renders only these).
  if (ev.model) view.model = ev.model;
  if (ev.cost) view.cost = Object.assign({}, view.cost, ev.cost);
  if (ev.measurable) view.measurable = ev.measurable;
  if (ev.activeMode || ev.active_mode) view.activeMode = ev.activeMode || ev.active_mode;
  if (ev.expires_at) view.expiresAt = ev.expires_at;
  if (ev.summary) view.lastSummary = ev.summary;
  if (ev.tool) view.lastTool = ev.tool;
  if (ev.diagnostics) view.diagnostics = ev.diagnostics;
  if (ev.review_state) view.reviewState = ev.review_state;

  switch (ev.type) {
    case "task.started": view.started = true; break;
    case "task.submitted": break;
    case "tool.started": view.activeTool = ev.tool || "tool"; break;
    case "tool.completed": case "tool.failed": view.activeTool = null; break;
    case "permission.required": view.approvalSeq = fp; break;
    case "permission.resolved": view.approvalSeq = null; break;
    case "task.finalizing": view.finishSeq = fp; break;
    case "model.call_failed":
      // Transient failure flash: the user just watched a step die. Any later
      // running/completed evidence recovers it; only task.failed makes it terminal.
      view.failureSeq = fp; view.failureN = view.events; view.retrySeq = -1; break;
    case "task.failed":
      view.terminal = "FAILED"; view.finishSeq = null; view.approvalSeq = null; break;
    case "task.cancelled":
      view.terminal = "CANCELLED"; view.finishSeq = null; view.approvalSeq = null; break;
    case "task.completed": {
      const rs = view.reviewState;
      // Terminal truth by verdict: PASS only on a real passed review; a reviewer that ran
      // without a verdict is VERIFIER_INCOMPLETE; ordinary completion WITHOUT a verifier is
      // its own honest state — completed, unreviewed — never UNKNOWN, never PASS (P2).
      view.terminal = rs === "passed" ? "PASS"
        : rs === "flagged" ? "FLAGGED"
        : (rs === "blocked" || rs === "degraded" || rs === "runtime_failed") ? "VERIFIER_INCOMPLETE"
        : "COMPLETED_UNREVIEWED";
      view.finishSeq = null; view.approvalSeq = null; break;
    }
    default: break;
  }
  if (cat === "RETRYING") { view.retrySeq = fp; view.failureSeq = -1; }

  // Category update. MODEL_GENERATION is only honest while no tool is outstanding
  // (doc 03 §3 row 10); during a tool it is part of that tool's work.
  if (cat && cat !== "APPROVAL_REQUIRED") {
    if (!(cat === "MODEL_GENERATION" && view.activeTool)) {
      view.category = cat; view.catFp = fp; view.catN = view.events;
      if (cat !== "UNDERSTANDING") view.middleSeen = true;
    }
  } else if (cat === "APPROVAL_REQUIRED") {
    view.category = "APPROVAL_REQUIRED"; view.catFp = fp; view.catN = view.events;
  }
  return true;
}
let vnLastChat = "";
function vnConsume(chatId, ev) {
  const view = vnView(chatId);
  const changed = vnApply(view, ev);
  vnLastChat = String(chatId || "");
  if (changed) vnSchedulePaint();
  return view;
}

// ---------------------------------------------------------------------------
// Resolve: one presentation object from the reducer state. Priority is the
// doc 02 §7 stack: terminal > approval lock > transient failure flash > retry >
// category. FINISH is the FINALIZING category, reachable ONLY via task.finalizing
// — without producer S1 there is no code path that can reach it.
function vnResolve(view) {
  const r = { state: "IDLE", override: null, category: null, phraseId: null, phraseText: "", tone: "idle", stage: "IDLE" };
  if (!view) return r;
  if (view.terminal) {
    r.state = view.terminal;
    r.override = view.terminal;
    r.tone = VN_OVERRIDE_TONES[view.terminal] || "idle";
    const fixed = VN_FIXED[view.terminal] || VN_FIXED.UNKNOWN;
    r.phraseId = fixed[0]; r.phraseText = fixed[1];
    r.stage = "TERMINAL";
    return r;
  }
  if (view.approvalSeq !== null) {
    r.state = "APPROVAL_REQUIRED"; r.override = "APPROVAL_REQUIRED";
    r.tone = VN_OVERRIDE_TONES.APPROVAL_REQUIRED;
    r.phraseId = VN_FIXED.APPROVAL_REQUIRED[0]; r.phraseText = VN_FIXED.APPROVAL_REQUIRED[1];
    r.stage = view.middleSeen ? "MIDDLE" : "START";
    return r;
  }
  if (view.failureSeq !== -1 && view.failureN >= view.catN) {
    // Transient failure flash: newest evidence is a failed step. Any newer category
    // evidence (fp compares as strings over the same monotone source) supersedes it,
    // so a recovered step shows recovery, not a stuck failure.
    r.state = "FAILED_FLASH"; r.category = null;
    r.tone = "failure";
    r.phraseId = VN_FIXED.FAILED[0]; r.phraseText = VN_FIXED.FAILED[1];
    r.stage = view.middleSeen ? "MIDDLE" : "START";
    return r;
  }
  const cat = view.category;
  if (cat === "FINALIZING") {
    r.state = "FINISH"; r.category = cat; r.tone = VN_CATEGORY_TONES.FINALIZING; r.stage = "FINISH";
  } else if (cat === "RETRYING") {
    r.state = "MIDDLE"; r.category = cat; r.tone = VN_CATEGORY_TONES.RETRYING; r.stage = "MIDDLE";
  } else if (cat) {
    r.state = "MIDDLE"; r.category = cat; r.tone = VN_CATEGORY_TONES[cat] || "active";
    r.stage = view.middleSeen ? "MIDDLE" : "START";
  } else if (view.started) {
    r.state = "START"; r.category = "UNDERSTANDING"; r.tone = "start"; r.stage = "START";
  }
  if (r.category) {
    const phrase = vnPhraseFor(view, r.category);
    r.phraseId = phrase[0]; r.phraseText = phrase[1];
  }
  return r;
}
// Deterministic phrase selection: the fingerprint is the category plus the seq (or the
// arrival counter for seq-less producers). Same evidence -> same phrase; new evidence
// -> a new phrase that never repeats the previous one while alternatives exist.
function vnPhraseFor(view, category) {
  const bucket = VN_LIBRARY[category];
  if (!bucket || !bucket.length) return [null, ""];
  const fp = category + ":" + (view.catFp || "");
  if (view.phraseCategory === category && view.rotation[category] === fp) {
    for (let i = 0; i < bucket.length; i++) if (bucket[i][0] === view.phraseId) return bucket[i];
  }
  let idx = vnFnv1a(fp) % bucket.length;
  const prev = view.phraseCategory === category ? view.phraseId : null;
  if (bucket.length > 1 && bucket[idx][0] === prev) idx = (idx + 1) % bucket.length;
  view.rotation[category] = fp;
  view.phraseCategory = category;
  view.phraseId = bucket[idx][0];
  return bucket[idx];
}"""

# ------------------------------------------------------------------- engine JS
_VN_ENGINE_JS = r"""// ---------------------------------------------------------------------------
// Sprite engine. States map to semantic sheet keys; loop states cycle, one-shot
// states advance once and hold their final frame (companion_render contract).
const VN_POS_KEY = "vool_ninja_pos_v1";
const VN_STATE_TIMING = { idle: 160, starting: 150, thinking: 140, tool: 110, waiting: 200, approval: 120, retry: 140 };
const VN_ONE_SHOT = { success: 1, failure: 1, cancelled: 1 };
// resolve() state -> sheet state. Unmapped events fall through silently (never improvise).
function vnSpriteState(pres) {
  switch (pres.state) {
    case "IDLE": return "idle";
    case "START": return "starting";
    case "FINISH": return "thinking";
    case "APPROVAL_REQUIRED": return "approval";
    case "FAILED": return "failure";
    case "FAILED_FLASH": return "failure";
    case "CANCELLED": return "cancelled";
    case "PASS": return "success";
    case "FLAGGED": return "approval";
    case "VERIFIER_INCOMPLETE": return "unknown";
    case "COMPLETED_UNREVIEWED": return "unknown";
    case "MIDDLE":
      if (pres.category === "RETRYING") return "retry";
      if (pres.category === "WAITING_QUEUED") return "waiting";
      if (pres.category === "WEB_RESEARCH" || pres.category === "READING_FILES" ||
          pres.category === "APPLYING_CHANGES" || pres.category === "GIT_INSPECTION" ||
          pres.category === "TESTING_CI" || pres.category === "RUNNING_TOOLS") return "tool";
      return "thinking";
    default: return "idle";
  }
}

// Layer + geometry state. POSITION IS PRESENTATION ONLY: it lives beside the sprite,
// never on a run/event/store object, and nothing in the drag path touches them.
const VN_SIZE = 112;
const VN_EDGE_INSET = 24;
const VN_CHIP_MAX = 640;
let vnLayer = null, vnSprite = null, vnCanvas = null, vnCtx = null, vnSr = null;
let vnPop = null, vnMenu = null, vnLab = null, vnRestore = null;
//: Minimum time one activity phrase stays readable before a newer phrase replaces it.
//: Presentation cadence ONLY (the pack allows wall-clock to animate cadence); it never creates,
//: delays or reorders execution truth, and terminal states ignore it.
const VN_CAPTION_MIN_MS = 900;
const vnCaptionHold = { text: "", at: 0 };
// Newest GROUNDED deferred phrase — text the reducer really resolved over a real typed event,
// held back only by the display dwell. Retention, never invention: it is painted at dwell
// expiry while the run is still non-terminal, and any terminal truth cancels it immediately.
const vnCaptionQueue = { phrase: null };
const VN_TERMINAL_STATES = { success: true, failure: true, cancelled: true, unknown: true, approval: true };
let vnPos = { v: 2, mode: "docked", previousMode: "docked", x: 0, y: 0, edge: null, hidden: false, personality: "spark", pack: "default", motion: "system" };
let vnDrag = null, vnMoveMode = false, vnEdgeSnap = false;
let vnPaint = { dirty: true, concept: "prism", state: "idle", frame: 0, stateStart: 0, lastPaint: 0, announced: "" };
let vnBooted = false, vnDesktopActive = false, vnDesktopSignature = "";

function vnPrefersReducedMotion() {
  // Manual override wins (three-state), else the system setting. Both keep state truth.
  if (vnPos.motion === "reduced") return true;
  if (vnPos.motion === "allow") return false;
  return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
}
function vnClampFree(x, y) {
  const vw = window.innerWidth || 1200, vh = window.innerHeight || 800;
  const nx = Math.min(Math.max(x, VN_EDGE_INSET), Math.max(VN_EDGE_INSET, vw - VN_SIZE - VN_EDGE_INSET));
  const ny = Math.min(Math.max(y, VN_EDGE_INSET), Math.max(VN_EDGE_INSET, vh - VN_SIZE - VN_EDGE_INSET));
  return [nx, ny];
}
function vnPersistPos() {
  // Clamp runs BEFORE persist: nothing off-screen can ever reach storage.
  if (vnPos.mode === "free") {
    const c = vnClampFree(vnPos.x, vnPos.y);
    vnPos.x = c[0]; vnPos.y = c[1];
  }
  try { localStorage.setItem(VN_POS_KEY, JSON.stringify(vnPos)); } catch (e) { /* storage unavailable: placement stays session-only */ }
}
function vnLoadPos() {
  try {
    const raw = localStorage.getItem(VN_POS_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return;
    if (parsed.mode === "free" || parsed.mode === "docked" || parsed.mode === "desktop") vnPos.mode = parsed.mode;
    if (parsed.previousMode === "free" || parsed.previousMode === "docked") vnPos.previousMode = parsed.previousMode;
    if (Number.isFinite(parsed.x)) vnPos.x = Number(parsed.x);
    if (Number.isFinite(parsed.y)) vnPos.y = Number(parsed.y);
    if (typeof parsed.edge === "string" || parsed.edge === null) vnPos.edge = parsed.edge;
    vnPos.hidden = parsed.hidden === true;
    if (window.VoolCompanionWorld && window.VoolCompanionWorld.characters[parsed.personality]) vnPos.personality = parsed.personality;
    if (window.VoolCompanionWorld && window.VoolCompanionWorld.packs[parsed.pack]) vnPos.pack = parsed.pack;
    if (parsed.motion === "system" || parsed.motion === "reduced" || parsed.motion === "allow") vnPos.motion = parsed.motion;
    // The detached pet's last native screen position, kept in the SAME preference as every other
    // companion placement value. The native side clamps it onto a display that exists today, so a
    // pet saved on an unplugged monitor is recovered rather than stranded off-screen.
    if (parsed.desktop && Number.isFinite(parsed.desktop.x) && Number.isFinite(parsed.desktop.y)) {
      vnPos.desktop = { x: Number(parsed.desktop.x), y: Number(parsed.desktop.y) };
    }
    vnEdgeSnap = parsed.edge === "snap";
  } catch (e) { /* corrupt or unavailable storage: lawful defaults */ }
  if (vnPos.mode === "free") { const c = vnClampFree(vnPos.x, vnPos.y); vnPos.x = c[0]; vnPos.y = c[1]; }
}

// Critical-rect registry: presentation data only. The sprite is never silently moved off a
// drop (one drag sets the final position, product/desktop-usability-20260917); a covering
// state is SHOWN so the operator can see what they are covering and move it.
// The docked home still slides clear of these (vnAvoidCritical) because it is a default, not
// a user drop.
// The attachment strip is a composer control like the textarea: at a phone width the docked
// sprite (right:22px, bottom:96px, 112px square) was measured sitting on a document chip's
// Preview button, which then could not be tapped.
const VN_CRITICAL_SELECTORS = ["#input", "#send", "#permBar", "#cloudPill", ".tc-stop", ".proj-menu", "#attachStrip", "#setupLine"];
function vnCoversCritical(x, y) {
  const rects = vnCriticalRects();
  for (let i = 0; i < rects.length; i++) {
    const r = rects[i];
    if (!(x + VN_SIZE <= r.left || x >= r.right || y + VN_SIZE <= r.top || y >= r.bottom)) return true;
  }
  return false;
}
// The docked home must respect the same critical rects a drop does: when the composer's
// controls reach up into it (a narrow window, an attachment strip), the sprite slides up to sit
// just above the highest control it would otherwise cover, then clamps to the window.
function vnAvoidCritical(c) {
  const rects = vnCriticalRects();
  // Moving above one control can land on a second control (the setup line above
  // the composer). Recheck the new position until it clears the stack. Each move
  // is strictly upward; clamping or exhausting the finite rectangles ends it.
  for (let pass = 0; pass < rects.length; pass++) {
    let top = null;
    for (let i = 0; i < rects.length; i++) {
      const r = rects[i];
      if (c[0] + VN_SIZE <= r.left || c[0] >= r.right || c[1] + VN_SIZE <= r.top || c[1] >= r.bottom) continue;
      if (top === null || r.top < top) top = r.top;
    }
    if (top === null) return c;
    const next = vnClampFree(c[0], top - VN_SIZE - 2);
    if (next[1] >= c[1]) return c;
    c = next;
  }
  return c;
}

function vnCriticalRects() {
  const out = [];
  for (let i = 0; i < VN_CRITICAL_SELECTORS.length; i++) {
    const el = document.querySelector(VN_CRITICAL_SELECTORS[i]);
    if (!el || typeof el.getBoundingClientRect !== "function") continue;
    const r = el.getBoundingClientRect();
    if (!r || !r.width || !r.height) continue;
    out.push(r);
  }
  return out;
}
function vnResolveDrop(x, y) {
  // ONE DRAG, ONE FINAL POSITION (product/desktop-usability-20260917): the drop lands where
  // the operator released it, bounded only by the window. The old contract slid the first
  // drop away from the composer's controls and honoured only a second attempt at the same
  // spot -- reported live as "one drag often fails to hold; a second drag is required".
  // Covering a control is still SHOWN (vnCoversCritical dims the sprite at rest and during
  // the drag); it is never silently moved. Docked-home avoidance (vnAvoidCritical) is a
  // different path and keeps protecting the DEFAULT home.
  return vnClampFree(x, y);
}

// ---------------------------------------------------------------------------
// Painting. rAF-coalesced presentation of the reducer's CURRENT truth: the periodic
// re-assert tick can only re-paint, never derive. No phrase swap happens without a
// consumed typed event having changed the reducer.
function vnSchedulePaint() { vnPaint.dirty = true; }
function vnApplyPos() {
  if (!vnSprite) return;
  // Pointer movement owns the live coordinates until the drop settles. The paint loop runs every
  // animation frame; without this guard it reapplied the old dock position between pointermove
  // events, producing the observed "few pixels, then snap back" failure.
  if (vnDrag && vnDrag.moved) return;
  const vw = window.innerWidth || 1200, vh = window.innerHeight || 800;
  vnSprite.classList.remove("chip");
  let covering = false;
  if (vnPos.mode === "docked") {
    // ux-pass1's authoritative home: right:22px; bottom:96px. Clamp only for tiny windows, and
    // never sit on a composer control (see vnAvoidCritical).
    const c = vnAvoidCritical(vnClampFree(vw - VN_SIZE - 22, vh - VN_SIZE - 96));
    vnSprite.style.left = c[0] + "px";
    vnSprite.style.top = c[1] + "px";
  } else {
    const c = vnClampFree(vnPos.x, vnPos.y);
    vnSprite.style.left = c[0] + "px";
    vnSprite.style.top = c[1] + "px";
    // A FREE position lands exactly where it was dropped; if it covers a composer control the
    // sprite stays visibly dimmed so the covering is known -- it is never silently moved.
    covering = vnCoversCritical(c[0], c[1]);
  }
  if (!vnDrag) vnSprite.classList.toggle("over-critical", covering);
  const onDesktop = vnPos.mode === "desktop" && vnDesktopActive;
  vnSprite.style.display = (vnPos.hidden || onDesktop) ? "none" : "flex";
  if (vnRestore) vnRestore.style.display = vnPos.hidden ? "block" : "none";
}
/* Live worker counts, per chat — fed ONLY from the monolith's ledger-derived agent rows
   (agent_node_started/completed). A scene is drawn when the DISPLAYED chat truly has that many
   live workers and the sprite is in an active state; entries expire so a dead stream can never
   leave a phantom crowd on screen. This is the one door multi-agent presentation goes through:
   no path invents workers. */
const VN_AGENTS = new Map();
const VN_AGENTS_TTL_MS = 45000;
function vnAgentsUpdate(chatId, runningCount) {
  const key = String(chatId || "");
  const count = Math.max(0, Number(runningCount) || 0);
  if (count > 0) VN_AGENTS.set(key, { count: count, at: Date.now() });
  else VN_AGENTS.delete(key);
  vnSchedulePaint();
}
function vnLiveAgentCount(chatId) {
  const row = VN_AGENTS.get(String(chatId || ""));
  if (!row) return 0;
  if (Date.now() - row.at > VN_AGENTS_TTL_MS) { VN_AGENTS.delete(String(chatId || "")); return 0; }
  return row.count;
}
const VN_SCENE_STATES = { tool: 1, thinking: 1, starting: 1, retry: 1 };
/* DEV-ONLY presentation puppet (Scene Lab). Overrides what the SPRITE draws — never the typed
   reducer, never a transcript, never worker counts. null = truth. The Scene Lab fragment is the
   only caller and is removed for beta by deleting its one mount line. */
var vnDevPuppet = null;
var vnCaption = null;
function vnFlashCaption() {
  if (!vnCaption) return;
  vnCaption.classList.add("vn-show");
  if (vnCaptionHold.timer) clearTimeout(vnCaptionHold.timer);
  vnCaptionHold.timer = setTimeout(function () { vnCaption.classList.remove("vn-show"); }, 2200);
}

function vnDrawFrame(state, frame) {
  if (!vnCtx) return;
  if (vnDevPuppet) {
    if (vnDevPuppet.scene && window.VoolCompanionWorld.scene
        && window.VoolCompanionWorld.scene(vnCtx, vnDevPuppet.scene, frame, {character:vnPos.personality,pack:vnPos.pack})) return;
    window.VoolCompanionWorld.draw(vnCtx, vnDevPuppet.state || state, frame, {character:vnPos.personality,pack:vnPos.pack,tone:vnPaint.tone});
    return;
  }
  if (VN_SCENE_STATES[state] && window.VoolCompanionWorld.scene) {
    const chatId = (typeof displayedChat !== "undefined" && displayedChat) ? displayedChat : vnLastChat;
    const workers = vnLiveAgentCount(chatId);
    if (workers >= 2) {
      const scene = workers >= 3 ? "huddle" : "pair";
      if (window.VoolCompanionWorld.scene(vnCtx, scene, frame, {character:vnPos.personality,pack:vnPos.pack})) return;
    }
  }
  window.VoolCompanionWorld.draw(vnCtx, state, frame, {character:vnPos.personality,pack:vnPos.pack,tone:vnPaint.tone});
  // Idle rare-moment: pure decoration, deterministic, long-idle only, motion-allowed only,
  // never while a puppet or any non-idle truth is on screen.
  if (state === "idle" && !vnDevPuppet && !vnPrefersReducedMotion() && window.VoolCompanionWorld.egg) {
    var idleMs = Date.now() - (vnPaint.stateStart || Date.now());
    if (idleMs > 360000) {
      var idleMinutes = Math.floor(idleMs / 60000);
      if (idleMinutes % 7 === 0) window.VoolCompanionWorld.egg(vnCtx, idleMinutes % 14 === 0 ? "coffee" : "zzz", frame);
    }
  }
}
function vnPaintNow(now) {
  if (!vnLayer) return;
  now = typeof now === "number" ? now : Date.now();
  const chatId = (typeof displayedChat !== "undefined" && displayedChat) ? displayedChat
    : (vnLastChat && VN_VIEWS.has(vnLastChat)) ? vnLastChat
    : (VN_VIEWS.size ? VN_VIEWS.keys().next().value : "");
  const view = VN_VIEWS.get(String(chatId || ""));
  const pres = vnResolve(view);
  vnPaint.tone = pres.tone;
  if (vnLayer.style.setProperty) vnLayer.style.setProperty("--vn-tone", VN_TONES[pres.tone] || VN_TONES.idle);
  // Sprite state always paints (browser cadence is already capped by the rAF loop, so
  // event bursts collapse into one painted change — presentation cadence only; the
  // reducer state itself is never gated or derived here).
  const nextState = vnSpriteState(pres);
  if (nextState !== vnPaint.state) { vnPaint.state = nextState; vnPaint.frame = 0; vnPaint.stateStart = now; }
  else if (!vnPaint.stateStart) { vnPaint.stateStart = now; }
  if (!vnPrefersReducedMotion()) {
    const frames = window.VoolCompanionWorld.frameCount(vnPos.personality, nextState) || 1;
    if (VN_ONE_SHOT[nextState]) vnPaint.frame = Math.min(Math.floor((now - vnPaint.stateStart) / 110), frames - 1);
    else vnPaint.frame = Math.floor((now - vnPaint.stateStart) / (VN_STATE_TIMING[nextState] || 160)) % frames;
  }
  vnDrawFrame(nextState, vnPaint.frame);
  // The caption carries the ACTIVITY LANGUAGE, not just the sprite state: the phrase is the
  // thing the operator reads ("Searching the web…", "Collecting facts on disk…"), and the pack's
  // colour law makes colour supplemental to a word that is always present. Falls back to the
  // typed state name when the reducer has no phrase (idle, or a fixed terminal state), so the
  // pill can never show a phrase the reducer did not resolve.
  if (vnCaption) {
    let captionText;
    if (vnDevPuppet && (vnDevPuppet.scene || vnDevPuppet.state)) {
      captionText = String(vnDevPuppet.scene || vnDevPuppet.state).toUpperCase() + ' \u00b7 DEV';
    } else if (pres && pres.phraseText) {
      captionText = String(pres.phraseText);
    } else {
      captionText = nextState.toUpperCase();
    }
    // DISPLAY DWELL — cadence only, never truth. Measured live 2026-08-29: a real turn resolved
    // its whole activity chain in ~1s (node durations 0.3s/0.4s), so every phrase appeared for a
    // few frames and the operator saw nothing but IDLE. The reducer still advances at event speed
    // and the phrase shown is always one the reducer really resolved; only the moment it is
    // REPLACED is held. Terminal truth bypasses the hold entirely: a failure, cancellation or
    // verdict must never wait behind a decorative phrase.
    // Judge the CURRENT painted state only. Reading `view.terminal` here was wrong: a view keeps
    // the terminal of the PREVIOUS turn, so every later phrase claimed to be terminal and skipped
    // the hold — the dwell silently did nothing (caught driving it live, 2026-08-29).
    const terminalNow = VN_TERMINAL_STATES[nextState] === true;
    // Terminal truth cancels deferred work outright: a working phrase must never resurface
    // after completion merely to satisfy animation timing (P2). Fast-turn activity remains
    // on the task-card line, which is not dwell-gated.
    if (terminalNow) vnCaptionQueue.phrase = null;
    const held = vnCaptionHold.text && (now - vnCaptionHold.at) < VN_CAPTION_MIN_MS;
    if (captionText !== vnCaptionHold.text) {
      if (held && !terminalNow) {
        vnCaptionQueue.phrase = captionText;  // retain the newest grounded deferred phrase
        vnSchedulePaint();  // come back when the hold expires; nothing is dropped
      } else {
        vnCaptionHold.text = captionText;
        vnCaptionHold.at = now;
        vnCaptionQueue.phrase = null;
        vnCaption.textContent = captionText;
        vnFlashCaption();
      }
    } else if (!held && vnCaptionQueue.phrase && !terminalNow) {
      // Dwell expired with a retained phrase still pending and the run STILL non-terminal:
      // paint it now. It was really resolved by the reducer; the hold only delayed the
      // moment its replacement became visible. A terminal resolve never reaches this arm —
      // it cleared the queue above and painted directly.
      const queued = vnCaptionQueue.phrase;
      vnCaptionQueue.phrase = null;
      vnCaptionHold.text = queued;
      vnCaptionHold.at = now;
      vnCaption.textContent = queued;
      vnFlashCaption();
    }
  }
  vnApplyPos();
  vnSyncDesktop(pres, nextState);
  // Card integration: the displayed card's stage line carries the phrase; the last
  // card in the transcript is the current turn. Text-only re-assertion of truth.
  const log = document.getElementById("log");
  if (log && log.querySelectorAll) {
    const stages = log.querySelectorAll(".task-card .tc-stage");
    const stage = stages && stages.length ? stages[stages.length - 1] : null;
    if (stage && pres.phraseText) {
      if (stage.textContent !== pres.phraseText) stage.textContent = pres.phraseText;
      if (stage.style && VN_TONES[pres.tone]) stage.style.color = VN_TONES[pres.tone];
    }
  }
  // aria-live: announce STATE changes only, once per change.
  if (vnSr && pres.phraseText) {
    const line = "Companion " + pres.state + (pres.phraseText ? " — " + pres.phraseText : "");
    if (line !== vnPaint.announced) { vnPaint.announced = line; vnSr.textContent = line; }
  }
  if (vnSprite) {
    vnSprite.setAttribute("aria-label", "Companion: " + pres.state + (pres.phraseText ? " · " + pres.phraseText : ""));
  }
  vnPaint.lastPaint = now;
  vnPaint.dirty = false;
}
function vnLoop() {
  if (document.hidden) { vnLoopTimer = null; return; }  // pause off-tab (house cadence law)
  if (vnPaint.dirty || !vnPrefersReducedMotion()) vnPaintNow(Date.now());
  vnLoopTimer = requestAnimationFrame(vnLoop);
}
let vnLoopTimer = null;
// Re-assert tick: re-paints CURRENT reducer truth over house card repaints. It reads
// state and paints; it can never derive, advance or rotate a phrase by itself.
setInterval(function () { if (vnBooted && !document.hidden) vnPaintNow(Date.now()); }, 1200);

// ---------------------------------------------------------------------------
// Popover: identity inspection from typed fields only. Provenance renders as data
// columns (actual > selected > requested/policy > recorded > unknown); unknown tiers
// render the literal word. No chain-of-thought surface exists in these payloads.
function vnProvRow(label, value) {
  return '<div class="vn-row"><b>' + label + '</b><span>' + esc(String(value == null || value === "" ? "unknown" : value)) + "</span></div>";
}
function vnRenderPopover(chatId) {
  if (!vnPop) return;
  const view = VN_VIEWS.get(String(chatId || ""));
  const pres = vnResolve(view);
  const m = (view && view.model) || {};
  const actual = m.actual_adapter_provider_id && m.actual_adapter_model_id
    ? m.actual_adapter_provider_id + "/" + m.actual_adapter_model_id : null;
  const selected = m.selected_provider_id && m.selected_model_id
    ? m.selected_provider_id + "/" + m.selected_model_id : null;
  const requested = (m.requested_provider_id || m.requested_model_id)
    ? (m.requested_provider_id || "?") + "/" + (m.requested_model_id || "?") : null;
  const policy = (m.policy_provider_id || m.policy_model_id)
    ? (m.policy_provider_id || "?") + "/" + (m.policy_model_id || "?") : null;
  const recorded = m.provider_id && m.model_id ? m.provider_id + "/" + m.model_id : null;
  const paid = m.paid === true ? "paid" : m.lane ? "free" : "unknown";
  let progress = "unknown";
  if (view && view.measurable && view.measurable.total) {
    progress = Math.round(1000 * (view.measurable.current || 0) / view.measurable.total) / 10 + "%";
  }
  const diag = (view && view.diagnostics) || {};
  let html = '<button type="button" class="vn-close" aria-label="Close">×</button>';
  html += "<h4>Worker</h4>";
  html += '<div class="vn-row"><b>identity</b><span>VOOL served lane</span></div>';
  html += '<div class="vn-row"><b>chat lane</b><span>' + esc(String(chatId || "unknown")) + "</span></div>";
  html += "<h4>Model provenance</h4>";
  html += vnProvRow("actual adapter", actual);
  html += vnProvRow("selected", selected);
  html += vnProvRow("requested", requested);
  html += vnProvRow("planned (policy)", policy);
  html += vnProvRow("recorded", recorded);
  html += vnProvRow("locality", m.locality);
  html += "<h4>Lane &amp; spend class</h4>";
  html += vnProvRow("lane", m.lane);
  html += vnProvRow("spend class", paid);
  html += "<h4>Current activity</h4>";
  html += '<div class="vn-row"><b>state</b><span>' + esc(pres.state) + "</span></div>";
  html += '<div class="vn-row"><b>category</b><span>' + esc(pres.category || "unknown") + "</span></div>";
  html += '<div class="vn-row"><b>status</b><span>' + esc(pres.phraseText || "unknown") + "</span></div>";
  html += "<h4>Progress</h4>";
  html += '<div class="vn-row"><b>measurable</b><span>' + esc(progress) + "</span></div>";
  html += "<h4>Result verdict</h4>";
  html += '<div class="vn-row vn-verdict"><b>verdict</b><span>' + esc(pres.state) + "</span></div>";
  if (view && view.reviewState) html += vnProvRow("review state", view.reviewState);
  if (diag && (diag.reason || diag.error_kind)) {
    html += "<h4>Failure cause</h4>";
    html += vnProvRow("reason", diag.reason);
    html += vnProvRow("error kind", diag.error_kind);
    if (diag.retryable === true) html += vnProvRow("recovery", "recoverable");
  }
  html += '<div class="vn-row" style="margin-top:8px"><button type="button" class="vn-ledger" style="width:100%;cursor:pointer">Open Activity ledger</button></div>';
  vnPop.innerHTML = html;
  vnPop.style.display = "block";
  if (vnSprite) {
    const sr = vnSprite.getBoundingClientRect();
    const pw = 300, vw = window.innerWidth || 1200, vh = window.innerHeight || 800;
    let px = sr.left - pw - 12;
    if (px < 8) px = Math.min(sr.right + 12, vw - pw - 8);
    const ph = Math.min(vh * 0.7, 480);
    let py = Math.min(Math.max(8, sr.top - 40), Math.max(8, vh - ph - 8));
    vnPop.style.left = Math.round(px) + "px";
    vnPop.style.top = Math.round(py) + "px";
  }
  const close = vnPop.querySelector(".vn-close");
  if (close && close.addEventListener) close.addEventListener("click", function () { vnPop.style.display = "none"; });
  const ledger = vnPop.querySelector(".vn-ledger");
  if (ledger && ledger.addEventListener) ledger.addEventListener("click", function () {
    vnPop.style.display = "none";
    if (typeof window.openPanel === "function") window.openPanel();
  });
}

// ---------------------------------------------------------------------------
// Drag / dock / hide. Pointer pipeline with capture; dragging mutates presentation
// geometry ONLY and can never reach run/event/store objects.
function vnBindDrag(el) {
  el.addEventListener("pointerdown", function (e) {
    if (vnPos.hidden) return;
    const start = { x: e.clientX, y: e.clientY, ox: parseFloat(el.style.left) || 0, oy: parseFloat(el.style.top) || 0, moved: false };
    vnDrag = start;
    el.setPointerCapture && el.setPointerCapture(e.pointerId);
    const move = function (ev) {
      if (!vnDrag) return;
      const dx = ev.clientX - vnDrag.x, dy = ev.clientY - vnDrag.y;
      if (!vnDrag.moved && Math.sqrt(dx * dx + dy * dy) < 6) return;  // click-vs-drag threshold
      vnDrag.moved = true;
      el.classList.add("dragging");
      const c = vnClampFree(vnDrag.ox + dx, vnDrag.oy + dy);
      el.style.left = c[0] + "px"; el.style.top = c[1] + "px";
      el.classList.toggle("over-critical", vnCoversCritical(c[0], c[1]));
    };
    const detach = function () {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", cancel);
    };
    const up = function (ev) {
      // These listeners live for ONE gesture. Leaving them attached made every later
      // pointerup anywhere in the app read as a sprite tap (vnDrag long null -> !wasMoved)
      // and reopen the status card on every click — the live 2026-08-28 defect.
      detach();
      if (!vnDrag) return;
      const wasMoved = vnDrag && vnDrag.moved;
      vnDrag = null;
      el.classList.remove("dragging");
      el.classList.remove("over-critical");
      if (!wasMoved) {
        if (vnPop && vnPop.style.display === "block") { vnPop.style.display = "none"; return; }
        vnRenderPopover(typeof displayedChat !== "undefined" ? displayedChat : "");
        return;
      }
      let nx = parseFloat(el.style.left) || 0, ny = parseFloat(el.style.top) || 0;
      if (vnEdgeSnap) {
        const vw = window.innerWidth || 1200, vh = window.innerHeight || 800;
        if (nx < 40) nx = VN_EDGE_INSET;
        if (nx > vw - VN_SIZE - 40) nx = vw - VN_SIZE - VN_EDGE_INSET;
        if (ny < 40) ny = VN_EDGE_INSET;
        if (ny > vh - VN_SIZE - 40) ny = vh - VN_SIZE - VN_EDGE_INSET;
      }
      // The released point IS the final position (clamped to the window only); the first
      // drag holds. No slide-away, no second-attempt override.
      const finalPos = vnResolveDrop(nx, ny);
      // Released hard against a window edge with the native bridge present: hand the
      // sprite to its desktop window instead of pinning it to the inside of the glass.
      const vwNow = window.innerWidth || 1200, vhNow = window.innerHeight || 800;
      const atEdge = finalPos[0] <= 2 || finalPos[1] <= 2
        || finalPos[0] + VN_SIZE >= vwNow - 2 || finalPos[1] + VN_SIZE >= vhNow - 2;
      if (atEdge && vnNativeCompanionApi()) { vnDetachDesktop(false); return; }
      vnPos.mode = "free"; vnPos.x = finalPos[0]; vnPos.y = finalPos[1];
      vnPersistPos();
      vnApplyPos();
    };
    const cancel = function () {
      detach();
      vnDrag = null;
      el.classList.remove("dragging");
      el.classList.remove("over-critical");
      vnApplyPos();  // revert to last stable position
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", cancel);
  });
}
function vnDock() {
  if (vnPos.mode === "desktop") { vnReturnToApp("docked"); return; }
  vnPos.mode = "docked";
  vnPersistPos(); vnApplyPos();
}
function vnHide(hidden) {
  vnPos.hidden = hidden === true;
  vnPersistPos(); vnApplyPos();
}
function vnCyclePersonality() {
  const keys = Object.keys(window.VoolCompanionWorld.characters);
  vnPos.personality = keys[(keys.indexOf(vnPos.personality) + 1) % keys.length] || "spark";
  vnPersistPos(); vnSchedulePaint(); vnPaintNow(Date.now());
}
function vnChooseCharacter(character) {
  if (!window.VoolCompanionWorld.characters[character]) return;
  vnPos.personality = character;
  vnPersistPos(); vnSchedulePaint(); vnPaintNow(Date.now());
  if (vnLab && vnLab.style.display !== "none") vnOpenCharacterLab();
}
function vnChoosePack(pack) {
  if (!window.VoolCompanionWorld.packs[pack]) return;
  vnPos.pack = pack;
  vnPersistPos(); vnSchedulePaint(); vnPaintNow(Date.now());
  if (vnLab && vnLab.style.display !== "none") vnOpenCharacterLab();
}
function vnCloseCharacterLab() {
  if (vnLab) vnLab.style.display = "none";
  if (vnSprite && !vnPos.hidden && vnPos.mode !== "desktop") vnSprite.focus();
}
function vnOpenCharacterLab() {
  if (!vnLab) return;
  vnLab.innerHTML = "";
  const modal = document.createElement("div");
  modal.className = "vn-lab-modal"; modal.setAttribute("role", "dialog"); modal.setAttribute("aria-modal", "true");
  modal.setAttribute("aria-labelledby", "vnLabTitle");
  const head = document.createElement("div"); head.className = "vn-lab-head";
  const copy = document.createElement("div");
  const title = document.createElement("h3"); title.id = "vnLabTitle"; title.textContent = "Character Lab";
  const sub = document.createElement("p"); sub.className = "vn-lab-sub";
  sub.textContent = "BigHead family · cosmetic only. Runtime state and outcome truth never change.";
  copy.appendChild(title); copy.appendChild(sub); head.appendChild(copy);
  const close = document.createElement("button"); close.type = "button"; close.className = "vn-lab-close";
  close.setAttribute("aria-label", "Close Character Lab"); close.textContent = "×"; close.addEventListener("click", vnCloseCharacterLab);
  head.appendChild(close); modal.appendChild(head);
  const grid = document.createElement("div"); grid.className = "vn-lab-grid";
  const keys = Object.keys(window.VoolCompanionWorld.characters);
  for (let i = 0; i < keys.length; i++) {
    const key = keys[i], meta = window.VoolCompanionWorld.characters[key];
    const card = document.createElement("button"); card.type = "button";
    card.className = "vn-character-card" + (vnPos.personality === key ? " selected" : "");
    card.setAttribute("aria-pressed", vnPos.personality === key ? "true" : "false");
    const preview = document.createElement("canvas"); preview.width = 48; preview.height = 48;
    const label = document.createElement("b"); label.textContent = meta.name;
    const NOTES = { spark: "engineer · balanced", rascal: "cheeky · expressive", prime: "voxel · polished",
      prism: "pixel ninja · teal crest", veil: "pixel ninja · amber scarf", ember: "pixel ninja · warm signal" };
    const note = document.createElement("small"); note.textContent = NOTES[key] || (meta.renderer === "sheet" ? "pixel sheet" : "voxel");
    card.appendChild(preview); card.appendChild(label); card.appendChild(note);
    card.addEventListener("click", (function (chosen) { return function () { vnChooseCharacter(chosen); }; })(key));
    grid.appendChild(card);
    const g = preview.getContext && preview.getContext("2d");
    if (g) { g.imageSmoothingEnabled = false; window.VoolCompanionWorld.draw(g, "idle", 0, {character:key,pack:vnPos.pack}); }
  }
  modal.appendChild(grid);
  const selectedMeta = window.VoolCompanionWorld.characters[vnPos.personality] || {};
  const packTitle = document.createElement("div"); packTitle.className = "vn-pack-title";
  packTitle.textContent = selectedMeta.renderer === "sheet"
    ? "Cosmetic palette — hand-drawn pixel families carry their own baked palette; packs apply to the voxel family"
    : "Cosmetic palette — never system truth";
  modal.appendChild(packTitle);
  const packGrid = document.createElement("div"); packGrid.className = "vn-pack-grid";
  const packNames = {default:"🧃 Default",ninja:"🥷 Ninja",moss:"🌿 Moss",cyber:"🤖 Cyber"};
  const packs = Object.keys(window.VoolCompanionWorld.packs);
  for (let j = 0; j < packs.length; j++) {
    const pack = packs[j], button = document.createElement("button"); button.type = "button";
    button.className = "vn-pack-card" + (vnPos.pack === pack ? " selected" : "");
    button.setAttribute("aria-pressed", vnPos.pack === pack ? "true" : "false"); button.textContent = packNames[pack] || pack;
    button.addEventListener("click", (function (chosen) { return function () { vnChoosePack(chosen); }; })(pack));
    packGrid.appendChild(button);
  }
  modal.appendChild(packGrid);
  // State matrix for the SELECTED character: every typed sprite state, painted by the real
  // renderer. A preview canvas, never the live sprite — the caption under each cell is the
  // typed state name, so art review reads against the state vocabulary, not vibes.
  const states = ["idle","starting","thinking","tool","waiting","approval","retry","success","failure","cancelled","unknown"];
  const matrixTitle = document.createElement("div"); matrixTitle.className = "vn-pack-title";
  matrixTitle.textContent = "State matrix — " + ((window.VoolCompanionWorld.characters[vnPos.personality] || {}).name || vnPos.personality);
  modal.appendChild(matrixTitle);
  const matrix = document.createElement("div"); matrix.className = "vn-lab-matrix";
  for (let si = 0; si < states.length; si++) {
    const cell = document.createElement("div"); cell.className = "vn-lab-cell";
    const cv = document.createElement("canvas"); cv.width = 48; cv.height = 48;
    const cap = document.createElement("small"); cap.textContent = states[si];
    cell.appendChild(cv); cell.appendChild(cap); matrix.appendChild(cell);
    const g2 = cv.getContext && cv.getContext("2d");
    if (g2) { g2.imageSmoothingEnabled = false; window.VoolCompanionWorld.draw(g2, states[si], 2, {character:vnPos.personality,pack:vnPos.pack}); }
  }
  modal.appendChild(matrix);
  // Silhouette readability test (the design authority's black-flood check): draw, then flood
  // the drawn pixels with one ink — a readable silhouette survives with no interior detail.
  const silTitle = document.createElement("div"); silTitle.className = "vn-pack-title";
  silTitle.textContent = "Silhouette test"; modal.appendChild(silTitle);
  const silRow = document.createElement("div"); silRow.className = "vn-lab-matrix";
  ["idle","tool","success","failure"].forEach(function (st) {
    const cell = document.createElement("div"); cell.className = "vn-lab-cell";
    const cv = document.createElement("canvas"); cv.width = 48; cv.height = 48;
    const cap = document.createElement("small"); cap.textContent = st;
    cell.appendChild(cv); cell.appendChild(cap); silRow.appendChild(cell);
    const g3 = cv.getContext && cv.getContext("2d");
    if (g3) {
      g3.imageSmoothingEnabled = false;
      window.VoolCompanionWorld.draw(g3, st, 0, {character:vnPos.personality,pack:vnPos.pack});
      g3.globalCompositeOperation = "source-atop"; g3.fillStyle = "#e8eaf0";
      g3.fillRect(0, 0, 48, 48); g3.globalCompositeOperation = "source-over";
    }
  });
  modal.appendChild(silRow);
  // Multi-worker scene preview — ILLUSTRATIVE ONLY, labelled as such. The live sprite shows a
  // scene exclusively when the runtime reports that many real workers (vnLiveAgentCount).
  const sceneTitle = document.createElement("div"); sceneTitle.className = "vn-pack-title";
  sceneTitle.textContent = "Worker scenes — preview only; the live sprite shows these exclusively when real workers run";
  modal.appendChild(sceneTitle);
  const sceneRow = document.createElement("div"); sceneRow.className = "vn-lab-matrix";
  [["pair", "2 workers"], ["huddle", "3+ workers"]].forEach(function (pairDef) {
    const cell = document.createElement("div"); cell.className = "vn-lab-cell";
    const cv = document.createElement("canvas"); cv.width = 48; cv.height = 48;
    const cap = document.createElement("small"); cap.textContent = pairDef[1];
    cell.appendChild(cv); cell.appendChild(cap); sceneRow.appendChild(cell);
    const g4 = cv.getContext && cv.getContext("2d");
    if (g4) { g4.imageSmoothingEnabled = false; window.VoolCompanionWorld.scene(g4, pairDef[0], 6, {character:vnPos.personality,pack:vnPos.pack}); }
  });
  modal.appendChild(sceneRow);
  vnLab.appendChild(modal); vnLab.style.display = "flex"; close.focus();
}
document.addEventListener("keydown", function (e) {
  if (e.key === "Escape" && vnPop && vnPop.style.display === "block") {
    e.preventDefault(); e.stopPropagation();
    vnPop.style.display = "none";
  }
}, true);
document.addEventListener("pointerdown", function (e) {
  if (!vnPop || vnPop.style.display !== "block") return;
  if (vnPop.contains(e.target)) return;
  if (vnSprite && vnSprite.contains(e.target)) return;  // the tap-toggle owns this case
  vnPop.style.display = "none";
}, true);

function vnNativeCompanionApi() {
  return window.pywebview && window.pywebview.api && window.pywebview.api.detach_companion ? window.pywebview.api : null;
}
function vnNotify(text) {
  if (typeof window.toast === "function") window.toast(text); else if (vnSr) vnSr.textContent = text;
}
function vnDesktopPayload(pres, state) {
  const payload = {state:state||vnSpriteState(pres),caption:pres&&pres.phraseText?pres.phraseText:String((pres&&pres.state)||"IDLE"),character:vnPos.personality,pack:vnPos.pack};
  if (vnPos.desktop && Number.isFinite(vnPos.desktop.x) && Number.isFinite(vnPos.desktop.y)) payload.desktop_position = vnPos.desktop;
  return payload;
}
/* The native side reports where the pet actually ended up; the page owns persistence. */
function vnDesktopMoved(position) {
  if (!position || !Number.isFinite(position.x) || !Number.isFinite(position.y)) return;
  vnPos.desktop = { x: Number(position.x), y: Number(position.y) };
  vnPersistPos();
}
function vnDesktopHidden() {
  vnDesktopActive = false; vnDesktopSignature = "";
  vnPos.hidden = true;
  vnPersistPos(); vnApplyPos();
}
async function vnResetDesktopPosition() {
  const api = vnNativeCompanionApi();
  if (!api || !api.reset_companion_position) { vnNotify("The pet is not on the desktop."); return; }
  try { await api.reset_companion_position(); vnNotify("Pet position reset."); } catch (e) { vnNotify("Could not reset the pet position."); }
}
async function vnHideDesktopPet() {
  const api = vnNativeCompanionApi();
  if (api && api.hide_companion) { try { await api.hide_companion(); return; } catch (e) { /* fall through to the in-app hide */ } }
  vnHide(true);
}
async function vnDetachDesktop(silent) {
  const api = vnNativeCompanionApi();
  if (!api) {
    vnDesktopActive = false;
    if (vnPos.mode === "desktop") vnPos.mode = vnPos.previousMode || "docked";
    vnPersistPos(); vnApplyPos();
    if (!silent) vnNotify("Desktop companion needs the native VOOL app window.");
    return false;
  }
  const chatId = (typeof displayedChat !== "undefined" && displayedChat) ? displayedChat : vnLastChat;
  const pres = vnResolve(VN_VIEWS.get(String(chatId||"")));
  try {
    const result = await api.detach_companion(vnDesktopPayload(pres,vnSpriteState(pres)));
    if (!result || result.ok !== true) throw new Error((result&&result.error)||"unavailable");
    if (vnPos.mode !== "desktop") vnPos.previousMode = vnPos.mode === "free" ? "free" : "docked";
    vnPos.mode = "desktop"; vnDesktopActive = true; vnDesktopSignature = "";
    vnPersistPos(); vnApplyPos(); vnSyncDesktop(pres,vnSpriteState(pres));
    if (!silent) vnNotify("Companion moved to the desktop. Drag it anywhere by its pixel world.");
    return true;
  } catch (e) {
    vnDesktopActive = false;
    if (vnPos.mode === "desktop") vnPos.mode = vnPos.previousMode || "docked";
    vnPersistPos(); vnApplyPos();
    if (!silent) vnNotify("Desktop companion could not open: "+((e&&e.message)||"unavailable"));
    return false;
  }
}
async function vnReturnToApp(mode) {
  const api = vnNativeCompanionApi();
  if (api && api.attach_companion) { try { await api.attach_companion(); } catch (e) {} }
  vnDesktopActive = false; vnDesktopSignature = "";
  vnPos.mode = mode === "free" ? "free" : mode === "docked" ? "docked" : (vnPos.previousMode === "free" ? "free" : "docked");
  vnPersistPos(); vnApplyPos(); vnSchedulePaint();
}
function vnDesktopReturned() {
  vnDesktopActive = false; vnDesktopSignature = "";
  vnPos.mode = vnPos.previousMode === "free" ? "free" : "docked";
  vnPersistPos(); vnApplyPos(); vnSchedulePaint();
}
function vnSyncDesktop(pres,state) {
  if (!vnDesktopActive) return;
  const api = vnNativeCompanionApi(); if (!api || !api.sync_companion) return;
  const payload = vnDesktopPayload(pres,state),sig=JSON.stringify(payload);
  if (sig === vnDesktopSignature) return;
  vnDesktopSignature=sig;Promise.resolve(api.sync_companion(payload)).catch(function(){vnDesktopSignature="";});
}
function vnCycleMotion() {
  vnPos.motion = vnPos.motion === "system" ? "reduced" : vnPos.motion === "reduced" ? "allow" : "system";
  document.body.classList.toggle("vn-motion-reduced", vnPrefersReducedMotion());
  vnPersistPos(); vnSchedulePaint();
}
function vnOpenMenu(x, y) {
  if (!vnMenu) return;
  vnMenu.innerHTML = "";
  const items = [
    ["Move to desktop", function () { vnDetachDesktop(false); }],
    ["Dock inside VOOL", vnDock],
    ["Character Lab…", vnOpenCharacterLab],
    ["Toggle edge snapping", function () { vnEdgeSnap = !vnEdgeSnap; vnPos.edge = vnEdgeSnap ? "snap" : null; vnPersistPos(); }],
    ["Hide companion", function () { if (vnPos.mode === "desktop") vnHideDesktopPet(); else vnHide(true); }],
    ["Reset pet position", vnResetDesktopPosition],
    ["Motion: " + vnPos.motion, vnCycleMotion],
    ["Companion: " + vnPos.personality, vnCyclePersonality],
    ["Activity detail", function () { vnRenderPopover(typeof displayedChat !== "undefined" ? displayedChat : ""); }],
  ];
  for (let i = 0; i < items.length; i++) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = items[i][0];
    const fn = items[i][1];
    b.addEventListener("click", function () { vnMenu.style.display = "none"; fn(); });
    vnMenu.appendChild(b);
  }
  vnMenu.style.display = "block";
  vnMenu.style.left = Math.min(x, (window.innerWidth || 1200) - 200) + "px";
  vnMenu.style.top = Math.min(y, (window.innerHeight || 800) - 280) + "px";
}
// Keyboard alternative: Tab reaches the sprite; Enter = move mode (arrows move, Shift =
// fine steps, Esc exits); D docks; H hides; Space opens the menu. Focus ring stays visible.
function vnBindKeyboard(el) {
  el.addEventListener("keydown", function (e) {
    if (vnMoveMode) {
      const step = e.shiftKey ? 4 : 32;
      if (e.key === "ArrowLeft" || e.key === "ArrowRight" || e.key === "ArrowUp" || e.key === "ArrowDown") {
        const dx = e.key === "ArrowLeft" ? -step : e.key === "ArrowRight" ? step : 0;
        const dy = e.key === "ArrowUp" ? -step : e.key === "ArrowDown" ? step : 0;
        const x = (parseFloat(el.style.left) || 0) + dx, y = (parseFloat(el.style.top) || 0) + dy;
        const c = vnClampFree(x, y);
        vnPos.mode = "free"; vnPos.x = c[0]; vnPos.y = c[1];
        vnPersistPos(); vnApplyPos();
        e.preventDefault();
      } else if (e.key === "Escape") { vnMoveMode = false; e.preventDefault(); }
      else if (e.key === "d" || e.key === "D") { vnDock(); vnMoveMode = false; e.preventDefault(); }
      else if (e.key === "h" || e.key === "H") { vnHide(true); vnMoveMode = false; e.preventDefault(); }
      return;
    }
    if (e.key === "Enter") { vnMoveMode = true; e.preventDefault(); }
    else if (e.key === " " || e.key === "Spacebar") {
      const r = el.getBoundingClientRect();
      vnOpenMenu(r.left, r.bottom + 4);
      e.preventDefault();
    }
    else if (e.key === "d" || e.key === "D") { vnDock(); e.preventDefault(); }
    else if (e.key === "h" || e.key === "H") { vnHide(true); e.preventDefault(); }
  });
}

// ---------------------------------------------------------------------------
// Boot: builds the layer (no body-markup edit), restores clamped placement, wires the
// window/visibility hooks. Idempotent; safe under any script evaluation order.
function vnBoot() {
  if (vnBooted) return;
  vnBooted = true;
  vnLoadPos();
  vnLayer = document.createElement("div");
  vnLayer.id = "companionLayer";
  vnSr = document.createElement("div");
  vnSr.className = "vn-sr";
  vnSr.setAttribute("aria-live", "polite");
  vnSprite = document.createElement("div");
  vnSprite.className = "vool-ninja";
  vnSprite.setAttribute("role", "button");
  vnSprite.setAttribute("tabindex", "0");
  vnSprite.setAttribute("aria-label", "Companion");
  vnCanvas = document.createElement("canvas");
  vnCanvas.width = 48; vnCanvas.height = 48;
  vnSprite.appendChild(vnCanvas);
  vnCaption = document.createElement("div");
  vnCaption.className = "vn-caption";
  vnCaption.textContent = "IDLE";
  vnSprite.appendChild(vnCaption);
  vnRestore = document.createElement("button");
  vnRestore.type = "button";
  vnRestore.className = "vn-restore";
  vnRestore.innerHTML = '<span class="vn-dot"></span>Companion';
  vnRestore.addEventListener("click", function () { vnHide(false); });
  vnPop = document.createElement("div");
  vnPop.className = "vool-ninja-pop";
  vnPop.style.display = "none";
  vnMenu = document.createElement("div");
  vnMenu.className = "vool-ninja-menu";
  vnMenu.style.display = "none";
  vnLab = document.createElement("div");
  vnLab.className = "vool-character-lab";
  vnLab.style.display = "none";
  vnLab.addEventListener("click", function (e) { if (e.target === vnLab) vnCloseCharacterLab(); });
  vnLayer.appendChild(vnSr);
  vnLayer.appendChild(vnSprite);
  vnLayer.appendChild(vnRestore);
  vnLayer.appendChild(vnPop);
  vnLayer.appendChild(vnMenu);
  vnLayer.appendChild(vnLab);
  document.body.appendChild(vnLayer);
  vnCtx = vnCanvas.getContext && vnCanvas.getContext("2d");
  if (vnCtx) vnCtx.imageSmoothingEnabled = false;
  vnBindDrag(vnSprite);
  vnBindKeyboard(vnSprite);
  vnSprite.addEventListener("contextmenu", function (e) {
    if (e.preventDefault) e.preventDefault();
    vnOpenMenu(e.clientX || 40, e.clientY || 40);
  });
  window.addEventListener("resize", function () { vnPersistPos(); vnApplyPos(); });
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden && vnLoopTimer == null && vnBooted) vnLoopTimer = requestAnimationFrame(vnLoop);
  });
  document.addEventListener("pywebviewready", function () { if (vnPos.mode === "desktop") vnDetachDesktop(true); });
  if (typeof ResizeObserver === "function") {
    const sb = document.getElementById("sidebar");
    if (sb) new ResizeObserver(function () { vnApplyPos(); }).observe(sb);
    // The composer's controls appear AFTER boot (a permission bar, an attachment strip, the setup
    // line): the footer grows, and the docked sprite must re-check the critical rects it may now be
    // covering. Measured 2026-09-07: the sprite sat on the setup line's button until this observer.
    const ft = document.querySelector("footer");
    if (ft) new ResizeObserver(function () { vnApplyPos(); }).observe(ft);
  }
  document.body.classList.toggle("vn-motion-reduced", vnPrefersReducedMotion());
  vnApplyPos();
  vnSchedulePaint();
  vnPaintNow(Date.now());
  vnLoopTimer = requestAnimationFrame(vnLoop);
  if (vnPos.mode === "desktop") setTimeout(function () { vnDetachDesktop(true); }, 250);
}
window.VoolCompanionBoot = vnBoot;
// The single typed-event entry the house consumer calls. Nothing else feeds the layer.
window.VoolCompanion = {
  consume: vnConsume,
  view: vnView,
  resolve: vnResolve,
  classify: vnClassify,
  phraseFor: vnPhraseFor,
  library: VN_LIBRARY,
  tones: VN_TONES,
  sheets: window.VoolCompanionWorld.sheets,
  characters: window.VoolCompanionWorld.characters,
  paintNow: function (t) { vnPaintNow(typeof t === "number" ? t : Date.now()); },
  applyPos: vnApplyPos,
  dock: vnDock,
  hide: vnHide,
  cyclePersonality: vnCyclePersonality,
  agents: vnAgentsUpdate,
  _devPuppet: function (spec) { vnDevPuppet = spec && (spec.state || spec.scene) ? { state: spec.state ? String(spec.state) : "", scene: spec.scene ? String(spec.scene) : "" } : null; vnSchedulePaint(); vnPaintNow(Date.now()); },
  liveAgentCount: vnLiveAgentCount,
  chooseCharacter: vnChooseCharacter,
  detachDesktop: function () { vnDetachDesktop(false); },
  choosePack: vnChoosePack,
  openCharacterLab: vnOpenCharacterLab,
  cycleMotion: vnCycleMotion,
  detachDesktop: vnDetachDesktop,
  returnToApp: vnReturnToApp,
  desktopReturned: vnDesktopReturned,
  desktopMoved: vnDesktopMoved,
  desktopHidden: vnDesktopHidden,
  resetDesktopPosition: vnResetDesktopPosition,
  hideDesktopPet: vnHideDesktopPet,
  pos: function () { return vnPos; },
  spriteCount: function () { return vnSprite && !vnPos.hidden ? 1 : (vnSprite ? 0 : 0); },
  popoverHtml: function (chatId) { vnRenderPopover(chatId); return vnPop ? vnPop.innerHTML : ""; },
  clamp: vnClampFree,
  resolveDrop: vnResolveDrop,
  loadPos: vnLoadPos,
  persistPos: vnPersistPos,
  paintState: function () { return vnPaint; },
  caption: function () { return vnCaption ? vnCaption.textContent : ""; },
  captionQueue: function () { return { phrase: vnCaptionQueue.phrase }; },
};
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", vnBoot);
else vnBoot();
"""


def render_companion_fragment() -> str:
    """The companion layer as ``<style> + <script>``, concatenated after the page."""
    from core.companion_world_fragment import COMPANION_WORLD_JS

    return (
        "<style>" + _VN_CSS + "</style>"
        + "<script>(function(){\n'use strict';\n"
        + COMPANION_WORLD_JS + "\n" + _VN_LIBRARY_JS + "\n" + _VN_ENGINE_JS
        + "\n})();</script>"
    )
