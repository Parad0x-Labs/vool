"""A freeze typed in chat from a phone, relay or bot needs the wallet PIN to undo, like the Freeze button."""
from __future__ import annotations

import pytest

from core.runtime_paths import active_vool_home, configure_runtime_home
from core.vool_agent_brake import maybe_handle_agent_brake
from core.wallet import limits
from core.wallet.errors import WalletFault


@pytest.fixture
def home(tmp_path):
    previous = active_vool_home()
    configure_runtime_home(tmp_path)
    try:
        yield tmp_path
    finally:
        configure_runtime_home(previous)


def test_remote_chat_freeze_cannot_be_undone_without_the_wallet_pin(home):
    reply = maybe_handle_agent_brake("/stopx402", "phone-chat", owner_local=False)
    assert reply is not None and reply["success"] is True
    assert "wallet PIN" in str(reply)
    assert limits.is_frozen() is True

    with pytest.raises(WalletFault) as refused:
        limits.set_frozen(False)
    assert refused.value.context.get("reason") == "freeze_requires_wallet_pin"

    resumed = maybe_handle_agent_brake("/startx402", "local", consent_fn=lambda _r: True)
    assert limits.is_frozen() is True
    assert resumed is not None and "wallet PIN" in str(resumed)
