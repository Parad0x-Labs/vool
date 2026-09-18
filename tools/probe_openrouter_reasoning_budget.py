"""Reproduce a grounded-synthesis call against OpenRouter and record what came back.

Runs inside a runtime profile (VOOL_HOME + the profile's key storage settings) so the OpenRouter
key is read through `core.credential_store` and never leaves this process; nothing secret is
printed. Each variant records finish_reason, usage (including reasoning token details when the
provider reports them), the response fields present on the message, and the first characters of
the content, so a truncated-reasoning failure is evidence rather than inference.

Usage:
    python tools/probe_openrouter_reasoning_budget.py --model nvidia/nemotron-3.5-lightning:free \
        --variant baseline:520 --variant exclude:520 --variant baseline:2048 --variant exclude:2048

A variant is `<kind>:<max_tokens>` where kind is `baseline` (no reasoning parameter) or `exclude`
(`reasoning: {"exclude": true}`, the documented OpenRouter control) or `effort-low`
(`reasoning: {"effort": "low"}`). Only free-tier models should be probed here unless the operator
has authorized spend.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

QUESTION = (
    "give me comparision review Vw passat vs vw golf, like how long they been maming it, "
    "total sale, most popuplar regions where sold, engines and so on"
)
SOURCES = [
    ("en.wikipedia.org/wiki/Volkswagen_Passat", "The Volkswagen Passat is a nameplate of large family cars (D-segment) manufactured and marketed by Volkswagen since 1973, also marketed as the Dasher, Santana, Quantum, Magotan, Corsar and Carat, in saloon and estate bodies."),
    ("en.wikipedia.org/wiki/Volkswagen_Golf", "The Volkswagen Golf is a compact car (C-segment) produced by Volkswagen since 1974, marketed worldwide across eight generations, in various body configurations and under various nameplates."),
    ("iseecars.com/compare/volkswagen-golf-vs-volkswagen-passat", "The Golf, a compact car, offers more interior volume: more front head room, rear head room and cargo space. The Passat, a midsize car, has the advantage in front shoulder room and rear leg room."),
    ("shopuslast.com/volkswagen-golf-vs-passat", "Both cars come with turbocharged four-cylinder petrol engines; the Passat is offered with larger engines and more standard equipment, the Golf with a wider range including GTI and R performance variants."),
]


def _messages() -> list[dict[str, str]]:
    evidence = "\n".join(f"- {text} Source: {url}" for url, text in SOURCES)
    return [
        {"role": "system", "content": "You are VOOL, a concise assistant. Answer only from the sources given; say what the sources do not cover."},
        {"role": "user", "content": f"{QUESTION}\n\nSources retrieved for this question:\n{evidence}"},
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--variant", action="append", default=[], help="<baseline|exclude|effort-low>:<max_tokens>")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()

    from core.credential_store import get_credential

    key = get_credential("llm.cloud.openrouter") or ""
    if not key.strip():
        print(json.dumps({"error": "no OpenRouter credential readable in this profile"}))
        return 2
    results = []
    for variant in args.variant or ["baseline:520"]:
        kind, _, budget = variant.partition(":")
        body: dict = {"model": args.model, "messages": _messages(), "max_tokens": int(budget or 520), "temperature": 0.2}
        if kind == "exclude":
            body["reasoning"] = {"exclude": True}
        elif kind == "effort-low":
            body["reasoning"] = {"effort": "low"}
        request = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                     "HTTP-Referer": "https://vool.local/probe", "X-OpenRouter-Title": "VOOL budget probe"},
            method="POST",
        )
        started = time.monotonic()
        record: dict = {"variant": variant, "model": args.model, "max_tokens": body["max_tokens"],
                        "reasoning_param": body.get("reasoning")}
        try:
            with urllib.request.urlopen(request, timeout=args.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            record["http_error"] = exc.code
            record["error_body"] = exc.read().decode("utf-8", "replace")[:300]
            results.append(record)
            print(json.dumps(record), flush=True)
            continue
        record["elapsed_s"] = round(time.monotonic() - started, 2)
        choice = (payload.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = str(message.get("content") or "")
        reasoning = message.get("reasoning")
        record.update({
            "finish_reason": choice.get("finish_reason"),
            "native_finish_reason": choice.get("native_finish_reason"),
            "usage": payload.get("usage"),
            "message_fields": sorted(message.keys()),
            "content_chars": len(content),
            "content_head": content[:240],
            "content_tail": content[-160:],
            "reasoning_chars": len(str(reasoning)) if reasoning else 0,
            "reasoning_details_count": len(message.get("reasoning_details") or []),
            "provider": payload.get("provider"),
        })
        results.append(record)
        print(json.dumps(record, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
