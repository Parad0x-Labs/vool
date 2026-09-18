"""Canonical prompt corpus for issue #5 routing-reliability acceptance."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RoutingCase:
    case_id: str
    prompt: str
    expected_route: str
    expected_intent: str = ""
    model_allowed: bool = False
    source_mode: str = "auto"
    live_safe: bool = False
    category: str = "routing_reliability"
    severity: str = ""
    setup: str = "isolated VoolAgent session with a disposable workspace"
    prompt_sequence: tuple[str, ...] = ()
    expected_behavior: str = ""
    forbidden_behavior: tuple[str, ...] = ()
    required_capability: str = ""
    expected_result_schema: str = "VoolAgent result with route, mode, model_execution, and tool_receipts"
    verification_method: str = "detector matrix plus VoolAgent.run_once end-to-end assertions"
    timeout_seconds: int = 30
    cleanup: str = "temporary workspace and isolated session state are discarded after the case"
    evidence_location: str = "reports/llm_eval/latest/routing_reliability.json"

    def __post_init__(self) -> None:
        capability = self.expected_intent or self.expected_route
        expected = {
            "arithmetic": "return the deterministic arithmetic result without model use",
            "machine": f"execute {self.expected_intent} and directly render its grounded result",
            "builder": "select model_build before generic model or tool routing",
            "model": "avoid issue #5 deterministic routes and use the conversational model lane",
            "workspace": "use the bounded workspace file lane without triggering an issue #5 route",
        }[self.expected_route]
        forbidden = {
            "arithmetic": ("model invocation", "web lookup", "fabricated calculation"),
            "machine": ("model fallback", "unscoped host mutation", "fabricated machine fact"),
            "builder": ("generic chat response", "unrelated machine tool", "workspace escape"),
            "model": ("arithmetic route", "machine route", "model_build route"),
            "workspace": ("arithmetic route", "machine route", "model_build route", "workspace escape"),
        }[self.expected_route]
        object.__setattr__(self, "severity", self.severity or ("critical" if self.expected_route == "machine" else "high"))
        object.__setattr__(self, "prompt_sequence", self.prompt_sequence or (self.prompt,))
        object.__setattr__(self, "expected_behavior", self.expected_behavior or expected)
        object.__setattr__(self, "forbidden_behavior", self.forbidden_behavior or forbidden)
        object.__setattr__(self, "required_capability", self.required_capability or capability)


POSITIVE_ROUTING_CASES = (
    RoutingCase("math-01", "What is 17 * 23?", "arithmetic", live_safe=True),
    RoutingCase("math-02", "what is 17 times 23?", "arithmetic", live_safe=True),
    RoutingCase("math-03", "50 times 3 please", "arithmetic", live_safe=True),
    RoutingCase("math-04", "100 / 4", "arithmetic", live_safe=True),
    RoutingCase("math-05", "Hi, what is the 100 x 50?", "arithmetic", live_safe=True),
    RoutingCase("math-06", "whats the value of 7*8", "arithmetic", live_safe=True),
    RoutingCase("math-07", "25 - 7 thanks", "arithmetic", live_safe=True),
    RoutingCase("math-08", "please calculate (9 + 3) * 2", "arithmetic", live_safe=True),
    RoutingCase("disk-01", "what's my total free space", "machine", "machine.disk_usage", live_safe=True),
    RoutingCase("disk-02", "how much free space do I have", "machine", "machine.disk_usage", live_safe=True),
    RoutingCase("disk-03", "how much storage is left", "machine", "machine.disk_usage", live_safe=True),
    RoutingCase("disk-04", "how much free space is on my drive?", "machine", "machine.disk_usage", live_safe=True),
    RoutingCase("disk-05", "what is my D drive total space?", "machine", "machine.disk_usage", live_safe=True),
    RoutingCase("disk-06", "Tell me free space on C:", "machine", "machine.disk_usage", live_safe=True),
    RoutingCase("disk-07", "total space used on C:", "machine", "machine.disk_usage", live_safe=True),
    RoutingCase("disk-08", "how much space is left on D:", "machine", "machine.disk_usage", live_safe=True),
    RoutingCase("specs-01", "what cpu does this machine have?", "machine", "machine.inspect_specs"),
    RoutingCase("specs-02", "how much RAM does my machine have?", "machine", "machine.inspect_specs"),
    RoutingCase("specs-03", "tell me my gpu", "machine", "machine.inspect_specs"),
    RoutingCase("specs-04", "what are this pc's specs?", "machine", "machine.inspect_specs"),
    RoutingCase("specs-05", "what operating system is this machine running?", "machine", "machine.inspect_specs"),
    RoutingCase("largest-01", "what's taking up the most space on C", "machine", "machine.find_largest"),
    RoutingCase("largest-02", "what are the biggest 5 folders on C drive?", "machine", "machine.find_largest"),
    RoutingCase("largest-03", "show me the largest files on my disk", "machine", "machine.find_largest"),
    RoutingCase("largest-04", "top folders by size on this PC", "machine", "machine.find_largest"),
    RoutingCase("largest-05", "what is the biggest single file on D drive?", "machine", "machine.find_largest"),
    RoutingCase("folder-01", "Find my Dropbox folder on this PC.", "machine", "machine.find_folder"),
    RoutingCase("folder-02", "where is my Documents folder on this PC?", "machine", "machine.find_folder"),
    RoutingCase("folder-03", "locate the Downloads directory on this machine", "machine", "machine.find_folder"),
    RoutingCase("folder-04", "find the Website V3 folder on this pc", "machine", "machine.find_folder"),
    RoutingCase(
        "build-01",
        "create a Python CLI app in a new folder with automated tests, then run the tests and build it",
        "builder",
        "model_build",
        model_allowed=True,
        source_mode="build",
    ),
    RoutingCase(
        "build-02",
        "build a small Python To-Do app in the workspace, include tests, run them, and verify the app",
        "builder",
        "model_build",
        model_allowed=True,
        source_mode="build",
    ),
    RoutingCase(
        "build-03",
        "implement a Python command-line notes app as multiple files with tests and build it here",
        "builder",
        "model_build",
        model_allowed=True,
        source_mode="build",
    ),
)


NEGATIVE_ROUTING_CASES = (
    RoutingCase("negative-01", "biggest movie this year", "model", model_allowed=True, live_safe=True),
    RoutingCase("negative-02", "write a poem about the ocean", "model", model_allowed=True, live_safe=True),
    RoutingCase("negative-03", "delete the last sentence", "model", model_allowed=True, live_safe=True),
    RoutingCase("negative-04", "what is the largest planet", "model", model_allowed=True, live_safe=True),
    RoutingCase("negative-05", "build a case for remote work", "model", model_allowed=True, live_safe=True),
    RoutingCase("negative-06", "free up space in my schedule", "model", model_allowed=True, live_safe=True),
    RoutingCase("negative-07", "is there any free space in my calendar today", "model", model_allowed=True, live_safe=True),
    RoutingCase("negative-08", "how much space is left in my cloud plan", "model", model_allowed=True, live_safe=True),
    RoutingCase("negative-09", "how much storage does my iCloud plan give me", "model", model_allowed=True, live_safe=True),
    RoutingCase("negative-10", "how much storage should a photo app offer", "model", model_allowed=True, live_safe=True),
    RoutingCase("negative-11", "compare cloud storage providers", "model", model_allowed=True, live_safe=True),
    RoutingCase("negative-12", "this plan is 50 times better", "model", model_allowed=True, live_safe=True),
    RoutingCase("negative-13", "which plan is 100 / 4 better for", "model", model_allowed=True, live_safe=True),
    RoutingCase("negative-14", "read the file config.json", "workspace", "workspace.read_file"),
    RoutingCase("negative-15", "create a file notes.txt with exactly this content: hi", "workspace", "workspace.write_file"),
    RoutingCase("negative-16", "show me pyproject.toml", "workspace", "workspace.read_file"),
)


ROUTING_RELIABILITY_CASES = POSITIVE_ROUTING_CASES + NEGATIVE_ROUTING_CASES
LIVE_ROUTING_CASES = tuple(case for case in ROUTING_RELIABILITY_CASES if case.live_safe)


assert len(POSITIVE_ROUTING_CASES) == 33
assert len(NEGATIVE_ROUTING_CASES) == 16
assert len(ROUTING_RELIABILITY_CASES) == 49
