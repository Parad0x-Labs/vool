"""Voice adapter — spoken-output lexicon and SSML hints.

Voice is a first-class surface: TTS reads strings aloud, so abbreviations,
codes, and status verdicts need speak-as expansions ("R$" -> "reais",
"blocked_false_claim" -> spoken phrase), and pauses need SSML breaks.

Exports a JSON lexicon per locale consumed by the TTS layer of any platform
(AVSpeechSynthesizer on Apple, TextToSpeech on Android, SAPI on Windows).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from vool_localization.adapters.base import PlatformAdapter

# canonical code -> per-locale spoken expansion (voice layer only; UI text
# keeps the compact form). Generated pass-001 data.
SPEAK_AS: dict[str, dict[str, str]] = {
    "en": {
        "clean": "verified clean",
        "no_action_claimed": "no action claimed",
        "blocked_false_claim": "false claim blocked",
        "unbacked_claim": "claim without evidence",
        "evidence_contradicted": "contradicted by evidence",
        "R$": "Brazilian reais",
        "null://": "null protocol",
    },
    "pt-BR": {
        "clean": "verificado e limpo",
        "no_action_claimed": "nenhuma ação reivindicada",
        "blocked_false_claim": "falso pedido bloqueado",
        "unbacked_claim": "pedido sem evidência",
        "evidence_contradicted": "contradito pelas evidências",
        "R$": "reais",
        "null://": "protocolo null",
    },
}

SSML_PAUSE_MS = {"sentence": 250, "clause": 120}


def ssml_wrap(text: str, *, lang: str, pause_after_sentence_ms: int | None = None) -> str:
    pause = pause_after_sentence_ms if pause_after_sentence_ms is not None else SSML_PAUSE_MS["sentence"]
    spoken = text
    for token, expansion in SPEAK_AS.get(lang, {}).items():
        spoken = spoken.replace(token, expansion)
    sentences = re.split(r"(?<=[.!?。])\s+", spoken.strip())
    body = (' <break time="%dms"/> ' % pause).join(sentences)
    return f'<speak xml:lang="{lang}">{body}</speak>'


class VoiceAdapter(PlatformAdapter):
    name = "voice"

    def export(self, locale: str, out_dir: Path) -> list[Path]:
        self._ensure_dir(out_dir)
        payload = {
            "locale": locale,
            "speak_as": SPEAK_AS.get(locale, {}),
            "ssml_pause_ms": SSML_PAUSE_MS,
        }
        path = out_dir / f"{locale}.voice.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return [path]
