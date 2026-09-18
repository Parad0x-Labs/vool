"""The semantic shape of each family this runtime understands. Vocabulary, never capability.

Deliberately separate from `core.conductor.registry`. That module answers "can this be EXECUTED";
this one answers "what is this frame MADE OF". Keeping them apart is what lets `fx_quote` be a
perfectly well-formed request with no operation behind it, and come to rest in a typed
CAPABILITY_UNAVAILABLE instead of looking like something the user never asked for.

The three consumers all read from here rather than each keeping their own table -- capture builds
slots from `roles`, projection resolves along `fan_out_role`, and the reader-facing sentence is
built from `display_roles`. Those were three separate dicts in three modules, which is three places
for one family to disagree about which role names its subject.
"""
from __future__ import annotations

from core.conductor.semantic_proof import FrameContract, register_frame_contract

BUILTIN_FRAME_CONTRACTS: tuple[FrameContract, ...] = (
    FrameContract(
        family="weather_lookup",
        roles=("location",),
        required_roles=("location",),
        fan_out_role="location",
        display_roles=("location",),
        output_contract="a current observation per named place",
    ),
    FrameContract(
        family="market_quote",
        roles=("asset_subject",),
        required_roles=("asset_subject",),
        fan_out_role="asset_subject",
        display_roles=("asset_subject",),
        output_contract="a current price per named asset",
    ),
    FrameContract(
        family="fx_quote",
        # One conversion is one indivisible piece of work: a base currency on its own is not a
        # conversion, so there is no fan-out role and no way to realize half of it.
        roles=("base_currency", "quote_currency", "amount"),
        required_roles=("base_currency", "quote_currency"),
        display_roles=("base_currency", "quote_currency"),
        display_join=" to ",
        output_contract="a directed rate or converted amount",
        # DELIBERATELY absent: `formal_proof_may_affirm`. A conversion reaches the network, and the
        # formal grammar cannot see "never" in the sentence it sits inside. Measured: "never convert
        # EUR to JPY" produced an AFFIRMED fx_quote. The formal path may still CAPTURE the frame --
        # it stays visible on the ledger as UNRESOLVED -- but only a bounded semantic proof can make
        # it executable.
    ),
    FrameContract(
        family="place_search",
        roles=("service", "location"),
        required_roles=("service", "location"),
        display_roles=("service", "location"),
        display_join=" in ",
        output_contract="local results for one service in one place",
    ),
    FrameContract(
        family="calculation",
        roles=("expression",),
        required_roles=("expression",),
        display_roles=("expression",),
        output_contract="one evaluated figure",
        # Local arithmetic. No network, no provider, no spend. The worst case of a closed grammar
        # affirming a sum the surrounding prose refused is one visible, cheap, wrong evaluation --
        # which is why this family is allowed to be affirmed by syntax alone and `fx_quote` is not.
        formal_proof_may_affirm=True,
    ),
    FrameContract(
        family="structured_field",
        roles=("field_name", "field_value"),
        required_roles=("field_name", "field_value"),
        display_roles=("field_name", "field_value"),
        display_join=": ",
        output_contract="the value of one named field",
        # An explicit `key: value` the user typed, read back locally. Same argument as calculation.
        formal_proof_may_affirm=True,
    ),
    FrameContract(
        # NO ordinary subject roles, on purpose. A derived frame's subject is the computation
        # itself, which is why it still needs exactly one realization: a requirement whose
        # realization count came from its argument sets had zero of them and therefore no outcome.
        family="quantitative_reasoning",
        roles=(),
        output_contract="a figure computed from earlier results",
    ),
)

#: Families whose SATISFIED result a derived frame may reason over.
OBSERVATION_FAMILIES = frozenset(
    {"market_quote", "weather_lookup", "fx_quote", "calculation", "structured_field"}
)


def register_builtin_frame_contracts(*, replace: bool = True) -> None:
    for contract in BUILTIN_FRAME_CONTRACTS:
        register_frame_contract(contract, replace=replace)


register_builtin_frame_contracts()

__all__ = [
    "BUILTIN_FRAME_CONTRACTS",
    "OBSERVATION_FAMILIES",
    "register_builtin_frame_contracts",
]
