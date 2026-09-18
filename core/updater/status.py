"""Plain-language status surface — what the user SEES during an update.

The wrapper UI renders `<data>/update_v2/status.json` (persisted, so "Update ready"
survives restarts). Phase → message mapping lives in ONE place; machine consumers get
the typed phase, humans get stable sentences with no internal jargon (no manifest
shas, no sequence numbers, no "CAS"/"journal" vocabulary).
"""
from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from core.updater.state import _atomic_write_json, _read_json


class UpdatePhase(Enum):
    IDLE = "idle"
    CHECKING = "checking"
    UP_TO_DATE = "up_to_date"
    READY = "ready"
    DOWNLOADING = "downloading"
    VERIFYING = "verifying"
    PREPARING = "preparing"
    BACKING_UP = "backing_up"
    READY_TO_RESTART = "ready_to_restart"
    INSTALLING = "installing"
    RESTARTING = "restarting"
    DONE = "done"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


def _fmt_mb(size: int) -> str:
    mb = size / (1024 * 1024)
    return f"{mb:.0f} MB" if mb >= 10 else f"{mb:.1f} MB"


def plain_message(phase: UpdatePhase, **context) -> str:
    version = str(context.get("version") or "")
    match phase:
        case UpdatePhase.IDLE:
            return "No update activity right now."
        case UpdatePhase.CHECKING:
            return "Checking for updates…"
        case UpdatePhase.UP_TO_DATE:
            return "You're up to date — no update is needed."
        case UpdatePhase.READY:
            return f"An update is ready: {version}. Press Update to install it."
        case UpdatePhase.DOWNLOADING:
            done = int(context.get("bytes_done") or 0)
            total = int(context.get("bytes_total") or 0)
            if total > 0:
                percent = min(100, round(done * 100 / total))
                return f"Downloading the update — {percent}% ({_fmt_mb(done)} of {_fmt_mb(total)})."
            return "Downloading the update…"
        case UpdatePhase.VERIFYING:
            return "Checking the download is complete and genuinely from us…"
        case UpdatePhase.PREPARING:
            return "Getting ready to install — pausing new work and checking the disk."
        case UpdatePhase.BACKING_UP:
            return "Saving a copy of the current version and your settings…"
        case UpdatePhase.READY_TO_RESTART:
            return (
                f"Update downloaded and checked — ready to restart into {version}. "
                "Press Restart to finish."
            )
        case UpdatePhase.INSTALLING:
            return "Installing the update. The app will restart in a moment."
        case UpdatePhase.RESTARTING:
            return "Restarting the app…"
        case UpdatePhase.DONE:
            return f"Update installed — you're now on {version}."
        case UpdatePhase.ROLLED_BACK:
            previous = str(context.get("previous_version") or "the previous version")
            return f"The update didn't finish, so {previous} was put back and restarted. Nothing was lost."
        case UpdatePhase.FAILED:
            detail = str(context.get("detail") or "something went wrong")
            return f"The update couldn't be installed: {detail}. The app was left as it was."
        case UpdatePhase.UNAVAILABLE:
            reason = str(context.get("reason") or "the update service isn't configured")
            return f"Updates are unavailable — {reason}."
    return "Updating…"  # pragma: no cover - exhaustive match


@dataclass
class UpdateStatus:
    phase: UpdatePhase
    message: str
    target_version: str = ""
    progress_percent: int | None = None
    fault_code: str = ""
    recovery_action: str = ""
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "phase": self.phase.value,
            "message": self.message,
            "target_version": self.target_version,
            "progress_percent": self.progress_percent,
            "fault_code": self.fault_code,
            "recovery_action": self.recovery_action,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> UpdateStatus | None:
        if not isinstance(data, dict):
            return None
        try:
            phase = UpdatePhase(str(data.get("phase")))
        except ValueError:
            return None
        progress = data.get("progress_percent")
        return cls(
            phase=phase,
            message=str(data.get("message") or ""),
            target_version=str(data.get("target_version") or ""),
            progress_percent=int(progress) if progress is not None else None,
            fault_code=str(data.get("fault_code") or ""),
            recovery_action=str(data.get("recovery_action") or ""),
            updated_at=float(data.get("updated_at") or 0.0),
        )


class UpdateFault(Enum):
    """The stable machine codes a FAILED / ROLLED_BACK status can carry, each with the
    ONE plain-language action the user can take next. The code is for logs, support and
    tests; the sentence is what the UI shows. Phase + message stay human."""

    NONE = ""
    DOWNLOAD_FAILED = "download_failed"
    DOWNLOAD_BLOCKED = "download_blocked"
    DISK_SPACE = "insufficient_disk_space"
    VERIFICATION_FAILED = "verification_failed"
    NOT_APPLICABLE = "update_not_applicable"
    INSTALL_FAILED = "install_failed"
    MIGRATION_FAILED = "migration_failed"
    HEALTH_CHECK_FAILED = "health_check_failed"
    DESTRUCTIVE_WORK_ACTIVE = "destructive_work_active"
    STALE_UPDATE_CLEANED_UP = "stale_update_cleaned_up"
    UNEXPECTED = "unexpected_failure"

    @property
    def recovery_action(self) -> str:
        return {
            UpdateFault.NONE: "",
            UpdateFault.DOWNLOAD_FAILED: (
                "Press Check for updates to try again — the download picks up where it stopped."
            ),
            UpdateFault.DOWNLOAD_BLOCKED: (
                "This app's network policy blocked the release server. Have the owner allow it, "
                "then press Check for updates."
            ),
            UpdateFault.DISK_SPACE: (
                "Free up disk space, then press Check for updates to try again — nothing was changed."
            ),
            UpdateFault.VERIFICATION_FAILED: (
                "Nothing was installed. Press Check for updates to fetch a fresh, verified copy."
            ),
            UpdateFault.NOT_APPLICABLE: (
                "No action is needed — there is nothing to install for this app right now."
            ),
            UpdateFault.INSTALL_FAILED: (
                "The previous version was left in place or put back. Press Check for updates to try again."
            ),
            UpdateFault.MIGRATION_FAILED: (
                "Your data was restored to the previous version. Please report this update problem."
            ),
            UpdateFault.HEALTH_CHECK_FAILED: (
                "The previous version is running again. Press Check for updates to try the update later."
            ),
            UpdateFault.DESTRUCTIVE_WORK_ACTIVE: (
                "Finish or close the work listed above, then press Restart to finish the update."
            ),
            UpdateFault.STALE_UPDATE_CLEANED_UP: (
                "No action is needed. Press Check for updates if you still want that update."
            ),
            UpdateFault.UNEXPECTED: (
                "Press Check for updates to try again. If it keeps failing, reinstall from the website."
            ),
        }[self]


class StatusStore:
    """Read/write the persisted status. Reads fail safe to None; writes are atomic."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def save(self, status: UpdateStatus) -> None:
        _atomic_write_json(self.path, status.to_dict())

    def load(self) -> UpdateStatus | None:
        return UpdateStatus.from_dict(_read_json(self.path))

    def publish(self, phase: UpdatePhase, **context) -> UpdateStatus:
        fault = context.get("fault")
        fault_code = fault.value if isinstance(fault, UpdateFault) else str(fault or "")
        recovery = str(context.get("recovery_action") or "")
        if not recovery and fault_code:
            with contextlib.suppress(ValueError):
                recovery = UpdateFault(fault_code).recovery_action
        status = UpdateStatus(
            phase=phase,
            message=plain_message(phase, **context),
            target_version=str(context.get("version") or ""),
            progress_percent=context.get("progress_percent"),
            fault_code=fault_code,
            recovery_action=recovery,
        )
        self.save(status)
        return status


__all__ = ["StatusStore", "UpdateFault", "UpdatePhase", "UpdateStatus", "plain_message"]
