"""R1g AMENDMENT — one typed secret intake BEFORE any lane owns the turn.

THE MEASURED DEFECT (base 610076c0)
-----------------------------------
All three of these finalized through a secret route and silently discarded the
ordinary demand beside it:

    "cloud key sk-or-… openrouter and explain entropy briefly"
      -> deterministic:cloud_key_command   (entropy NEVER RAN)
    "image key abc…:xyz… and explain entropy briefly"
      -> deterministic:image_key_command    (same swallow)
    "sk-or-v1-… and explain entropy briefly"
      -> deterministic:bare_secret_intercept (same swallow)

The R1g demand gate deliberately exempted the secret-consuming intents ("a key
pasted beside a question must be consumed here, never handed to a model"), and
the exemption's SECURITY half was right — the bytes may go nowhere but the
credential store — while its OWNERSHIP half was wrong: consuming the credential
is one owned outcome of the turn, not a license to finalize the whole external
turn and drop every other demand in the same message.

THE CONTRACT
------------
`consume_secret_intake` runs before any lane, before the task_received event,
before any model or planner can see the text. It returns ONE typed result:

* ``disposition`` — ``handled`` (a credential route produced its safe reply),
  ``refused`` (non-owner: refused, nothing stored), or ``sanitized_only``
  (nothing claimed; the text merely carries secret-shaped material and is
  redacted for the rest of the cascade).
* ``reply`` — the credential-side response the existing handler computed (the
  handlers keep owning detection, the store and the wording — unchanged).
* ``consumed_spans`` — the EXACT half-open spans carved out of the original
  message (empty for ``sanitized_only``).
* ``remainder`` — the message with the consumed spans removed and any OTHER
  secret-shaped material redacted by the persistence layer's own authority
  (``core.secret_redaction``). This is the only text the rest of the turn may
  see: children, model input, planner tasks, receipts, events.
* ``failure`` — a handler-side exception string (side effects may already have
  happened; the caller treats a reply-bearing intake as owned, never as None).

The caller decides: no remainder demand -> today's fast response, byte for
byte; remainder demand -> the credential outcome and the remainder's units run
as owned outcomes of the SAME canonical turn (see ``VoolAgent
._answer_secret_intake_turn``); ``sanitized_only`` -> the cascade continues on
the redacted text and nothing finalizes here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: Dispositions (see the module docstring).
DISPOSITION_HANDLED = "handled"
DISPOSITION_REFUSED = "refused"
DISPOSITION_SANITIZED = "sanitized_only"
#: The intake could not prove the text safe (its own machinery failed). The
#: caller fails CLOSED on this disposition: the typed reply ships, nothing
#: else runs, and the raw text reaches no lane, model, child, event or log.
DISPOSITION_FAILED = "failed"

#: The one safe failure reply — static by construction, so it can never echo
#: any part of the message that could not be processed.
SAFE_FAILURE_REPLY = (
    "I could not process this message safely just now — it may contain a "
    "credential I could not handle, so I stopped without running anything and "
    "nothing was stored. Please retry, or set the key from your own local "
    "session with `cloud key <your-key>`."
)

#: Intake route ids — these finalize (when they finalize) under the
#: ``secret_intake_turn`` family in ``core.lane_registry``.
ROUTE_CLOUD_KEY = "cloud_key_command"
ROUTE_IMAGE_KEY = "image_key_command"
ROUTE_BARE_SECRET = "bare_secret_intercept"
ROUTE_SECRET_BEARING = "secret_bearing"

_TOKEN_RE = re.compile(r"\S+")


@dataclass(frozen=True)
class SecretIntake:
    """One turn's secret intake — typed, immutable."""

    disposition: str
    route: str
    reply: str = ""
    consumed_spans: tuple[tuple[int, int], ...] = ()
    remainder: str = ""
    failure: str = ""
    #: Computed at consumption (inside the caller's guarded region) so the
    #: decision never re-runs mint machinery on a partially-processed turn.
    remainder_has_demand: bool = False

    @property
    def consumed(self) -> bool:
        """Whether a credential route actually claimed part of the message."""
        return bool(self.reply)


def _sanitize(value: str, spans: tuple[tuple[int, int], ...]) -> str:
    """`value` with every consumed span cut out and the rest redacted.

    The redaction is the persistence layer's own high-precision authority
    (``core.secret_redaction``) — the same one the chat log already relies on —
    so a pasted config's OTHER secrets are masked before any model sees them,
    and ordinary prose survives untouched."""
    from core.secret_redaction import redact_secrets

    cut = list(value)
    for start, end in sorted(spans, reverse=True):
        cut[start:end] = " "
    redacted = redact_secrets(" ".join("".join(cut).split()))
    return redacted.strip()


def _failed_intake(route: str, failure: str) -> SecretIntake:
    """The fail-closed intake: a static safe reply, an EMPTY remainder, no
    demand — the caller ships the reply and runs nothing else. Used when the
    intake's own machinery cannot prove the carve geometry (a degenerate
    zero-length consumed span — the matcher and the text disagree about what
    was consumed, so NOTHING may be handed on)."""
    return SecretIntake(
        disposition=DISPOSITION_FAILED,
        route=route,
        reply=SAFE_FAILURE_REPLY,
        consumed_spans=(),
        remainder="",
        failure=failure[:200],
        remainder_has_demand=False,
    )


def _command_intake(
    value: str,
    match: Any,
    *,
    route: str,
    owner_local: bool,
    handler: Any,
    qualifier_consumed: Any,
) -> SecretIntake:
    """The shared cloud/image command intake: carve the command + secret (+
    a recognized qualifier) out of the message, hand the handler the COMMAND
    ALONE so its store/detect/ask logic runs on exactly what was consumed, and
    keep the rest of the user's words as the remainder."""
    arg_region = match.group(1) or ""
    arg_start = match.start(1) if arg_region else match.end()
    tokens = [
        (token_match.group(0), arg_start + token_match.start(), arg_start + token_match.end())
        for token_match in _TOKEN_RE.finditer(arg_region)
    ]
    consumed_text_parts: list[str] = []
    # R1g2 amendment, defect 1: a NO-VALUE command ("cloud key", "image key")
    # has no secret to carve — the handler's own explain/status reply IS the
    # answer, the consumed span is the full command match, the remainder is
    # EMPTY and zero children run. At base this computed a zero-length span,
    # left the command words as a "remainder", minted them as demand and ran
    # the merged executor on them.
    consumed_end = match.end() if not tokens else match.start()
    for index, (token, _start, end) in enumerate(tokens):
        part = token
        if index == 0:
            pass  # the secret (or forget word) itself is always consumed, raw
        elif index == 1 and qualifier_consumed(token):
            # a recognized provider / model id rides the command; the comma it
            # was glued to in prose is not part of it
            part = token.strip(".,;:!?")
        else:
            break  # everything from here is the user's next sentence
        consumed_text_parts.append(part)
        consumed_end = end
    if consumed_end <= match.start():
        # Degenerate geometry (a matcher whose match covers nothing): the
        # carve cannot be proven, so nothing raw may be handed on.
        return _failed_intake(
            route, f"degenerate consumed span ({match.start()}, {consumed_end})"
        )
    command_prefix = value[match.start() : arg_start] if tokens else value.strip()
    credential_text = " ".join([command_prefix.strip(), *consumed_text_parts]).strip()
    failure = ""
    reply = ""
    try:
        reply = str(handler(credential_text, owner_local=owner_local) or "")
    except Exception as exc:  # the store's own failure must not resurrect the secret
        failure = f"{type(exc).__name__}: {exc}"[:200]
        reply = "I could not process the credential just now, so nothing was stored."
    remainder = _sanitize(value, ((match.start(), consumed_end),))
    return SecretIntake(
        disposition=DISPOSITION_REFUSED if not owner_local else DISPOSITION_HANDLED,
        route=route,
        reply=reply,
        consumed_spans=((match.start(), consumed_end),),
        remainder=remainder,
        failure=failure,
        remainder_has_demand=bool(remainder) and _has_demand(remainder),
    )


def _has_demand(remainder: str) -> bool:
    """Whether the sanitized remainder mints at least one demand unit."""
    try:
        from core.agent_runtime.answer_coverage import demand_units

        return bool(demand_units(remainder))
    except Exception:
        return True  # a mint failure must not finalize the turn as credential-only


def consume_secret_intake(text: str, *, owner_local: bool) -> SecretIntake | None:
    """The one typed intake over a turn's raw text, or None when no secret route
    applies and the text carries no secret-shaped material at all."""
    from core.agent_runtime.fast_command_surface import (
        bare_key_span,
        cloud_key_command_match,
        image_key_command_match,
    )

    value = str(text or "")
    if not value.strip():
        return None

    cloud_match = cloud_key_command_match(value)
    if cloud_match is not None:
        return _cloud_command_intake(value, cloud_match, owner_local=owner_local)
    image_match = image_key_command_match(value)
    if image_match is not None:
        return _image_command_intake(value, image_match, owner_local=owner_local)
    span = bare_key_span(value)
    if span is not None:
        return _bare_key_intake(value, span, owner_local=owner_local)

    try:
        from core.secret_redaction import contains_secret

        if contains_secret(value):
            return SecretIntake(
                disposition=DISPOSITION_SANITIZED,
                route=ROUTE_SECRET_BEARING,
                reply="",
                consumed_spans=(),
                remainder=_sanitize(value, ()),
                remainder_has_demand=False,  # nothing finalizes on this disposition
            )
    except Exception:
        return None
    return None


def _cloud_command_intake(value: str, match: Any, *, owner_local: bool) -> SecretIntake:
    from core.agent_runtime.fast_command_surface import (
        CLOUD_KEY_FORGET_WORDS,
        maybe_handle_cloud_key_command,
    )

    def _cloud_qualifier(token: str) -> bool:
        # A trailing provider names the store the key belongs to; a forget word
        # IS the command. Anything else is the user's next sentence.
        lowered = token.strip(".,;:!?").lower()
        try:
            from core.cloud_providers import PROVIDERS

            if lowered in PROVIDERS:
                return True
        except Exception:
            pass
        return lowered in CLOUD_KEY_FORGET_WORDS

    return _command_intake(
        value,
        match,
        route=ROUTE_CLOUD_KEY,
        owner_local=owner_local,
        handler=maybe_handle_cloud_key_command,
        qualifier_consumed=_cloud_qualifier,
    )


def _image_command_intake(value: str, match: Any, *, owner_local: bool) -> SecretIntake:
    from core.agent_runtime.fast_command_surface import maybe_handle_image_key_command

    def _image_qualifier(token: str) -> bool:
        # fal model ids carry a provider path ("fal-ai/flux/dev"); an ordinary
        # word after the key is the user's next sentence, not a model.
        return "/" in token

    return _command_intake(
        value,
        match,
        route=ROUTE_IMAGE_KEY,
        owner_local=owner_local,
        handler=maybe_handle_image_key_command,
        qualifier_consumed=_image_qualifier,
    )


def _bare_key_intake(value: str, span: tuple[int, int], *, owner_local: bool) -> SecretIntake:
    from core.agent_runtime.fast_command_surface import maybe_handle_bare_secret

    key_text = value[span[0] : span[1]]
    failure = ""
    reply = ""
    try:
        reply = str(maybe_handle_bare_secret(key_text, owner_local=owner_local) or "")
    except Exception as exc:
        failure = f"{type(exc).__name__}: {exc}"[:200]
        reply = "I could not process the credential just now, so nothing was stored."
    remainder = _sanitize(value, (span,))
    return SecretIntake(
        disposition=DISPOSITION_REFUSED if not owner_local else DISPOSITION_HANDLED,
        route=ROUTE_BARE_SECRET,
        reply=reply,
        consumed_spans=(span,),
        remainder=remainder,
        failure=failure,
        remainder_has_demand=bool(remainder) and _has_demand(remainder),
    )


__all__ = [
    "DISPOSITION_FAILED",
    "DISPOSITION_HANDLED",
    "DISPOSITION_REFUSED",
    "DISPOSITION_SANITIZED",
    "ROUTE_BARE_SECRET",
    "ROUTE_CLOUD_KEY",
    "ROUTE_IMAGE_KEY",
    "ROUTE_SECRET_BEARING",
    "SAFE_FAILURE_REPLY",
    "SecretIntake",
    "consume_secret_intake",
]
