from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class CallTiming:
    on_dispatch: object = field(default=None, repr=False)
    dispatched: float | None = None
    first_token: float | None = None
    finished: float | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def dispatch(self) -> None:
        with self.lock:
            if self.dispatched is None:
                if callable(self.on_dispatch):
                    self.on_dispatch()
                self.dispatched = time.monotonic()

    def token(self) -> None:
        with self.lock:
            if self.dispatched is not None and self.first_token is None:
                self.first_token = time.monotonic()

    def finish(self) -> None:
        with self.lock:
            if self.dispatched is not None:
                self.finished = time.monotonic()

    def public(self) -> dict[str, float | None]:
        with self.lock:
            return {"first_token_ms": (self.first_token - self.dispatched) * 1000
                    if self.first_token is not None and self.dispatched is not None else None,
                    "total_ms": (self.finished - self.dispatched) * 1000
                    if self.finished is not None and self.dispatched is not None else None}
