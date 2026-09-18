"""Local draft store: the sanitized draft is durable BEFORE anything is approved or sent.

Drafts live under ``<store>/drafts/<report_id>.json`` (atomic tmp+rename writes). Loading
goes through the strict schema parser, so a file tampered with on disk -- an injected
``conversation`` field, an edited ``actual`` -- either fails loudly or changes the
recomputed payload hash, which the approval check then refuses.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from core.bug_report.schema import BugReportDraft, DraftNotFoundError

_REPORT_ID_RE = re.compile(r"^br_[0-9a-f]{12}$")


def is_valid_report_id(report_id: str) -> bool:
    return bool(_REPORT_ID_RE.match(str(report_id or "")))


class DraftStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.drafts_dir = self.root / "drafts"

    def _path_for(self, report_id: str) -> Path:
        if not is_valid_report_id(report_id):
            raise DraftNotFoundError(f"invalid report id: {report_id!r}")
        return self.drafts_dir / f"{report_id}.json"

    def save(self, draft: BugReportDraft) -> Path:
        path = self._path_for(draft.report_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(draft.to_dict(), ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        return path

    def load(self, report_id: str) -> BugReportDraft:
        path = self._path_for(report_id)
        if not path.exists():
            raise DraftNotFoundError(f"no local draft for {report_id!r}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise DraftNotFoundError(f"draft file for {report_id!r} is corrupt: {exc}") from exc
        return BugReportDraft.from_dict(data)

    def ids(self) -> list[str]:
        if not self.drafts_dir.exists():
            return []
        return sorted(p.stem for p in self.drafts_dir.glob("br_*.json"))


__all__ = ["DraftStore", "is_valid_report_id"]
