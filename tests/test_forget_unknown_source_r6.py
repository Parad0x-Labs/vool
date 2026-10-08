"""Unknown tiered carriers never bypass revocation authority."""
import pytest

import core.tiered_context_loader as tiered
from core.prompt_assembly_report import ContextItem


@pytest.mark.parametrize("origin", [None, "source-chat"])
def test_unknown_source_type_is_withheld_even_with_origin(origin):
    item = ContextItem(
        item_id="future-cache", layer="relevant", source_type="future_memory_cache",
        title="Cached fact", content="My locker code is MINT-84.",
        metadata={"origin_chat_id": origin} if origin else {},
    )
    kept, excluded = tiered._forget_safe_context_items([item])
    assert kept == []
    assert excluded == [(item, "forget_source_type_unrecognized")]


def test_static_policy_survives_unknown_carrier_rejection():
    policy = ContextItem(item_id="policy", layer="bootstrap", source_type="policy",
                         title="Policy", content="Use approved tools.")
    unknown = ContextItem(item_id="cache", layer="relevant", source_type="new_cache",
                          title="Cache", content="MINT-84")
    kept, excluded = tiered._forget_safe_context_items([policy, unknown])
    assert kept == [policy]
    assert excluded == [(unknown, "forget_source_type_unrecognized")]
