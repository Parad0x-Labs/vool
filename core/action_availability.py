"""Typed, deterministic recognition of actions this runtime cannot perform.

This module does not decide safety policy and does not execute anything.  It recognizes effect
requests that require embodiment, an outbound emergency channel, or authority over physical
infrastructure.  Those are capability facts: when no matching typed side-effect operation exists,
the honest result is ``available=False`` and ``attempted=False`` rather than a speculative tool
call or a fluent claim that the action happened.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from enum import Enum


class UnavailableActionKind(str, Enum):
    PHYSICAL_EMBODIMENT = "physical_embodiment"
    OUTBOUND_EMERGENCY = "outbound_emergency"
    PHYSICAL_DEVICE_CONTROL = "physical_device_control"
    INFRASTRUCTURE_CONTROL = "infrastructure_control"
    OUTBOUND_COMMUNICATION = "outbound_communication"
    DESTRUCTIVE_SYSTEM_ACTION = "destructive_system_action"
    UNAUTHORIZED_INTRUSION = "unauthorized_intrusion"
    EXPLOSIVE_EMERGENCY = "explosive_emergency"
    UNAUTHORIZED_REMOTE_DESTRUCTION = "unauthorized_remote_destruction"
    OUTBOUND_TRAVEL = "outbound_travel"
    PHYSICAL_OBSERVATION = "physical_observation"
    SOFTWARE_DEPLOYMENT = "software_deployment"
    WEAPON_DEPLOYMENT = "weapon_deployment"
    PROTECTED_SELF_MODIFICATION = "protected_self_modification"
    PHYSICAL_ENVIRONMENT_CONTROL = "physical_environment_control"


@dataclass(frozen=True)
class ActionAvailabilityReport:
    request: str
    kind: UnavailableActionKind
    available: bool = False
    attempted: bool = False
    reason: str = ""
    next_step: str = ""

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["kind"] = self.kind.value
        return payload


_PHYSICAL_MARKER_RE = re.compile(
    r"\b(?:physically|physical\s+world|in\s+person|with\s+(?:your|a)\s+(?:body|hands?))\b",
    re.IGNORECASE,
)
_EMERGENCY_OUTBOUND_RE = re.compile(
    r"\b(?:call|dial|phone|contact|notify|alert|message|text|summon)\b"
    r"[^.!?;\n]{0,80}\b(?:9[ -]?1[ -]?1|112|999|emergency\s+services?|"
    r"fire\s+department|police|ambulance|paramedics?)\b",
    re.IGNORECASE,
)
_PERSONAL_OUTBOUND_RE = re.compile(
    r"\b(?:send|write)\b[^.!?;\n]{0,40}\b(?:text|message|sms|email|e-mail)\b"
    r"|^\s*(?:(?:please|now|immediately)\s+)*(?:text|message|email|e-mail)\b"
    r"[^.!?;\n]{0,90}\bmy\b",
    re.IGNORECASE,
)
_VOICE_OUTBOUND_RE = re.compile(
    r"^\s*(?:(?:please|now|immediately)\s+)*(?:call|phone|dial)\b"
    r"[^.!?;\n]{0,100}\b(?:on\s+the\s+phone|by\s+phone|tell\s+(?:them|him|her)|"
    r"speak\s+(?:to|with))\b",
    re.IGNORECASE,
)
_OUTBOUND_SPEECH_CONTINUATION_RE = re.compile(
    r"^\s*(?:(?:please|now|immediately)\s+)*tell\s+(?:them|him|her)\b[^.!?;\n]+",
    re.IGNORECASE,
)
_BROAD_DESTRUCTIVE_RE = re.compile(
    r"\b(?:sudo\s+)?rm\s+(?:-[^\s]*r[^\s]*f[^\s]*|-[^\s]*f[^\s]*r[^\s]*|"
    r"-r\s+-f|-f\s+-r)\s+(?:/|/\*|~|\$HOME)(?:[`'\"\s]|$)"
    r"|^\s*(?:(?:please|now|immediately)\s+)*(?:delete|remove|erase|wipe|destroy)\b"
    r"[^.!?;\n]{0,70}\b(?:all|every|entire)\b[^.!?;\n]{0,70}"
    r"\b(?:files?|folders?|directory|directories|disk|drive|database|system|workspace)\b",
    re.IGNORECASE,
)
_UNAUTHORIZED_INTRUSION_RE = re.compile(
    r"^\s*(?:(?:please|now|immediately)\s+)*(?:hack|breach|compromise|break\s+into|intrude\s+into)\b"
    r"[^.!?;\n]{0,100}\b(?:account|computer|server|network|mainframe|system|pentagon|government|bank)\b",
    re.IGNORECASE,
)
_UNAUTHORIZED_REMOTE_DESTRUCTION_RE = re.compile(
    r"^\s*(?:(?:please|now|immediately)\s+)*(?:delete|drop|erase|wipe|destroy|remove)\b"
    r"(?=[^.!?;\n]{0,200}\b(?:database|server|account|system|service)\b)"
    r"(?:(?=[^.!?;\n]{0,200}\bremote\b)"
    r"|(?=[^.!?;\n]{0,200}\b(?:root|production|primary|master)\b)"
    r"(?=[^!?;\n]{0,200}\b(?:of|on|at|from)\s+"
    r"(?:https?://)?[a-z0-9.-]+\.[a-z]{2,}\b))",
    re.IGNORECASE,
)
_EXPLOSIVE_EMERGENCY_RE = re.compile(
    r"^\s*(?:(?:please|now|immediately|physically)\s+)*"
    r"(?:defuse|disarm|deactivate|dismantle|render\s+safe)\b"
    r"[^.!?;\n]{0,100}\b(?:bomb|explosive|device|ordnance)\b",
    re.IGNORECASE,
)
_BODILY_ACTION_RE = re.compile(
    r"\b(?:do|perform|complete)\b[^.!?;\n]{0,50}"
    r"\b(?:push[ -]?ups?|sit[ -]?ups?|squats?|burpees?|exercise|jumping\s+jacks?|"
    r"backflips?|somersaults?)\b"
    r"|\bphysically\b[^.!?;\n]{0,30}\b(?:punch|slap|kick|hug|carry|lift)\b",
    re.IGNORECASE,
)
_PHYSICAL_CARE_RE = re.compile(
    r"^\s*(?:(?:please|now|immediately|physically)\s+)*(?:feed|walk|groom|bathe)\b"
    r"[^.!?;\n]{0,80}\b(?:physical\s+)?(?:pet|dog|cat|animal)\b",
    re.IGNORECASE,
)
_TRAVEL_DELIVERY_RE = re.compile(
    r"^\s*(?:(?:please|now|immediately|physically)\s+)*(?:fly|travel|walk|go|come)\b"
    r"[^.!?;\n]{0,80}\b(?:to|into|over|across|outside|kitchen|bedroom|roof|window)\b"
    r"(?:[^.!?;\n]{0,100}\b(?:bring|fetch|carry|deliver|check)\b)?",
    re.IGNORECASE,
)
_VEHICLE_ACTION_RE = re.compile(
    r"\b(?:drive|park|steer|move)\b[^.!?;\n]{0,70}"
    r"\b(?:car|vehicle|truck|motorcycle|bike|bicycle|it)\b",
    re.IGNORECASE,
)
_POUR_OR_SERVE_RE = re.compile(
    r"\b(?:pour|serve|bring|hand)\b[^.!?;\n]{0,60}"
    r"\b(?:coffee|tea|water|drink|cup|glass|mug)\b",
    re.IGNORECASE,
)
_PHYSICAL_DEVICE_ACTION_RE = re.compile(
    r"\b(?:turn|switch|shut|power|unplug|plug|load|insert|remove|open|close|start|stop|"
    r"restart|reboot|print|bake|fly|land|launch|take)"
    r"(?:\s+(?:off|on|down|up))?\b[^.!?;\n]{0,80}"
    r"\b(?:router|modem|printer|oven|stove|microwave|toaster|thermostat|lamp|lights?|"
    r"door|garage|appliance|laptop|computer|workstation|webcam|camera|drone|quadcopter)\b",
    re.IGNORECASE,
)
_PHYSICAL_DEVICE_CONTINUATION_RE = re.compile(
    r"^\s*(?:(?:please|now|immediately)\s+)*(?:fly|land|steer|park|move)\s+it\b"
    r"[^.!?;\n]{0,70}\b(?:roof|ground|window|outside|inside|yard|driveway)\b",
    re.IGNORECASE,
)
_PHYSICAL_OBSERVATION_RE = re.compile(
    r"^\s*(?:(?:please|now|physically|actually)\s+)*(?:check|inspect|observe|look\s+at|see)\b"
    r"[^.!?;\n]{0,90}\b(?:stove|oven|door|window|room|kitchen|appliance|pet|dog|cat)\b"
    r"[^.!?;\n]{0,50}\b(?:on|off|open|closed|there|safe|okay|ok|working|running)?\b",
    re.IGNORECASE,
)
_SOFTWARE_DEPLOYMENT_RE = re.compile(
    r"^\s*(?:(?:please|now|immediately)\s+)*(?:deploy|provision|install|launch)\b"
    r"[^.!?;\n]{0,100}\b(?:live\s+)?(?:cluster|service|application|app|stack|workload)\b"
    r"[^.!?;\n]{0,80}\b(?:local|physical|machine|computer|host|server)\b",
    re.IGNORECASE,
)
_WEAPON_DEPLOYMENT_RE = re.compile(
    r"^\s*(?:(?:please|now|immediately)\s+)*(?:deploy|launch|fire|detonate)\b"
    r"[^.!?;\n]{0,100}\b(?:nuclear|weapon|warhead|missile|bomb|explosive)\b",
    re.IGNORECASE,
)
_PROTECTED_SELF_MODIFICATION_RE = re.compile(
    r"^\s*(?:(?:please|now|immediately)\s+)*(?:rewrite|modify|edit|replace|rename)\b"
    r"[^.!?;\n]{0,80}\b(?:your|runtime(?:'s)?|assistant(?:'s)?)\b"
    r"[^.!?;\n]{0,80}\b(?:internal|core|system|runtime|source)\b"
    r"[^.!?;\n]{0,50}\b(?:code|instructions?|identity|name)\b",
    re.IGNORECASE,
)
_GRAVITY_CONTROL_RE = re.compile(
    r"^\s*(?:(?:please|now|immediately)\s+)*(?:turn|switch|shut|disable|remove|reverse)\b"
    r"(?:\s+(?:off|on|down|up))?[^.!?;\n]{0,60}\bgravity\b"
    r"[^.!?;\n]{0,60}\b(?:room|bedroom|house|building|area|city|earth)?\b",
    re.IGNORECASE,
)
_INFRASTRUCTURE_ACTION_RE = re.compile(
    r"\b(?:turn|switch|shut|cut|take|power|disconnect)"
    r"(?:\s+(?:off|on|down|out))?\b[^.!?;\n]{0,90}"
    r"\b(?:electricity|power|gas|water|grid|utility|utilities)\b[^.!?;\n]{0,50}"
    r"\b(?:neighbou?rhood|street|block|building|city|town|district|area|community)\b"
    r"|\b(?:neighbou?rhood|street|block|building|city|town|district|area|community)\b"
    r"[^.!?;\n]{0,50}\b(?:electricity|power|gas|water|grid|utility|utilities)\b",
    re.IGNORECASE,
)
_PHYSICAL_ACTION_HEAD_RE = re.compile(
    r"^\s*(?:(?:please|now|immediately|actually)\s+)*"
    r"(?:pour|serve|bring|hand|do|perform|complete|drive|park|steer|move|turn|switch|shut|"
    r"power|unplug|plug|load|insert|remove|open|close|start|stop|restart|reboot|destroy|"
    r"smash|break|burn|punch|slap|kick|feed|fly|walk|deploy|launch|rewrite|modify)\b",
    re.IGNORECASE,
)
_NON_REQUEST_ACTION_RE = re.compile(
    r"^\s*(?:do\s+not|don't|never|avoid|refuse\s+to)\b"
    r"|^\s*(?:the\s+)?(?:prompt|example|test|message|story|sentence|documentation)\b"
    r"[^.!?;\n]{0,80}\b(?:says?|contains?|mentions?|quotes?|describes?)\b"
    r"|^\s*(?:explain|describe|define|summari[sz]e|what\s+does|what\s+is|how\s+does)\b",
    re.IGNORECASE,
)


def unavailable_action_report(text: str) -> ActionAvailabilityReport | None:
    """Return an unavailable effect report, or ``None`` for an unrecognized action.

    Recognition is deliberately conjunctive: an action verb plus an effect domain.  Words such as
    ``router``, ``printer`` or ``drive`` alone do not acquire meaning from a benchmark vocabulary;
    "explain how a printer works", "run the router test", and "drive the test suite" remain outside
    this boundary.
    """

    request = " ".join(str(text or "").strip().split())
    if not request:
        return None
    if _NON_REQUEST_ACTION_RE.search(request):
        return None
    if _EMERGENCY_OUTBOUND_RE.search(request):
        return ActionAvailabilityReport(
            request=request,
            kind=UnavailableActionKind.OUTBOUND_EMERGENCY,
            reason="this runtime has no verified telephone or emergency-dispatch channel",
            next_step=(
                "If anyone may be in immediate danger, use a phone or another verified local "
                "channel to contact emergency services now."
            ),
        )
    if _EXPLOSIVE_EMERGENCY_RE.search(request):
        return ActionAvailabilityReport(
            request=request,
            kind=UnavailableActionKind.EXPLOSIVE_EMERGENCY,
            reason="this runtime has no physical bomb-disposal capability and cannot make the device safe",
            next_step=(
                "Do not touch or approach it; move away, warn others, and contact local emergency "
                "services from a safe location."
            ),
        )
    if _UNAUTHORIZED_REMOTE_DESTRUCTION_RE.search(request):
        return ActionAvailabilityReport(
            request=request,
            kind=UnavailableActionKind.UNAUTHORIZED_REMOTE_DESTRUCTION,
            reason="no verified authority or control channel exists for destructive changes to that remote system",
        )
    if _UNAUTHORIZED_INTRUSION_RE.search(request):
        return ActionAvailabilityReport(
            request=request,
            kind=UnavailableActionKind.UNAUTHORIZED_INTRUSION,
            reason="unauthorized intrusion is not an available or permitted runtime effect",
        )
    if _BROAD_DESTRUCTIVE_RE.search(request):
        return ActionAvailabilityReport(
            request=request,
            kind=UnavailableActionKind.DESTRUCTIVE_SYSTEM_ACTION,
            reason="this broad destructive system effect is not available from the chat boundary",
        )
    if _PERSONAL_OUTBOUND_RE.search(request):
        return ActionAvailabilityReport(
            request=request,
            kind=UnavailableActionKind.OUTBOUND_COMMUNICATION,
            reason="no verified outbound messaging channel and recipient binding are available",
        )
    if _VOICE_OUTBOUND_RE.search(request) or _OUTBOUND_SPEECH_CONTINUATION_RE.search(request):
        return ActionAvailabilityReport(
            request=request,
            kind=UnavailableActionKind.OUTBOUND_COMMUNICATION,
            reason="no verified outbound telephone channel and recipient binding are available",
        )
    if _WEAPON_DEPLOYMENT_RE.search(request):
        return ActionAvailabilityReport(
            request=request,
            kind=UnavailableActionKind.WEAPON_DEPLOYMENT,
            reason="no authorized or available capability can deploy or operate a physical weapon",
        )
    if _PROTECTED_SELF_MODIFICATION_RE.search(request):
        return ActionAvailabilityReport(
            request=request,
            kind=UnavailableActionKind.PROTECTED_SELF_MODIFICATION,
            reason="the chat boundary has no typed authority to rewrite the runtime's protected implementation",
        )
    if _SOFTWARE_DEPLOYMENT_RE.search(request):
        return ActionAvailabilityReport(
            request=request,
            kind=UnavailableActionKind.SOFTWARE_DEPLOYMENT,
            reason="no typed deployment capability for that machine or target is registered",
        )
    if _GRAVITY_CONTROL_RE.search(request):
        return ActionAvailabilityReport(
            request=request,
            kind=UnavailableActionKind.PHYSICAL_ENVIRONMENT_CONTROL,
            reason="this runtime has no capability to alter a physical gravitational field",
        )
    if _INFRASTRUCTURE_ACTION_RE.search(request):
        return ActionAvailabilityReport(
            request=request,
            kind=UnavailableActionKind.INFRASTRUCTURE_CONTROL,
            reason="this runtime has no control authority over public or building utilities",
        )
    if _PHYSICAL_OBSERVATION_RE.search(request):
        return ActionAvailabilityReport(
            request=request,
            kind=UnavailableActionKind.PHYSICAL_OBSERVATION,
            reason="no verified sensor or physical observer is available in the user's environment",
        )
    if _PHYSICAL_DEVICE_ACTION_RE.search(request) or _PHYSICAL_DEVICE_CONTINUATION_RE.search(request):
        return ActionAvailabilityReport(
            request=request,
            kind=UnavailableActionKind.PHYSICAL_DEVICE_CONTROL,
            reason="no typed control capability for that physical device is available",
        )
    if (
        _BODILY_ACTION_RE.search(request)
        or _PHYSICAL_CARE_RE.search(request)
        or _VEHICLE_ACTION_RE.search(request)
        or _POUR_OR_SERVE_RE.search(request)
    ):
        return ActionAvailabilityReport(
            request=request,
            kind=UnavailableActionKind.PHYSICAL_EMBODIMENT,
            reason="this runtime has no physical body or actuator in the user's environment",
        )
    if _TRAVEL_DELIVERY_RE.search(request):
        return ActionAvailabilityReport(
            request=request,
            kind=UnavailableActionKind.OUTBOUND_TRAVEL,
            reason="this runtime has no body or verified travel and physical-delivery capability",
        )
    if _PHYSICAL_MARKER_RE.search(request) and _PHYSICAL_ACTION_HEAD_RE.search(request):
        return ActionAvailabilityReport(
            request=request,
            kind=UnavailableActionKind.PHYSICAL_EMBODIMENT,
            reason="this runtime has no physical body or actuator in the user's environment",
        )
    return None


__all__ = [
    "ActionAvailabilityReport",
    "UnavailableActionKind",
    "unavailable_action_report",
]
