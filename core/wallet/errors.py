"""The wallet's one exception: a typed fault code plus a public-safe context.

Every refusal the wallet makes is a :class:`WalletFault` whose ``code`` is declared in the
runtime's single fault catalog (``core.faults.catalog``). The context carries identifiers a
user or operator may see -- proposal ids, limits, networks -- and never key material, PINs,
recovery phrases or card data; producers are written so those values cannot reach it.
"""
from __future__ import annotations

from typing import Any


class WalletFault(Exception):  # noqa: N818 - named for the fault plane's vocabulary (a typed fault), like FaultError's record
    def __init__(self, code: str, *, context: dict[str, Any] | None = None, fault_id: str = "", message: str = "") -> None:
        self.code = str(code)
        self.context: dict[str, Any] = dict(context or {})
        self.fault_id = str(fault_id or "")
        self.user_message = str(message or "")
        super().__init__(self.user_message or self.code)

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "fault_id": self.fault_id, "user_message": self.user_message, "context": dict(self.context)}
