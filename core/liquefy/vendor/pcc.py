# VENDORED SNAPSHOT — DO NOT EDIT IN PLACE.
# Source: OX-LIQUEFY (Parad0x Labs, MIT) src/liquefy/pcc.py
# Pinned upstream commits: 2936ecc (audited lineage) through 822bd0b (HEAD). Snapshot date: 2026-09-02.
# Local modifications: none.
"""
Per-Column Commitment (PCC) — the keystone of the Verifiable Evidence Layer.
============================================================================

Publish a single 32-byte root that pins the exact contents of every column in a
structured (JSONL / list-of-dict) dataset. Later, PROVE that one column holds a
specific set of values — and DISCLOSE only that column — without revealing the
rest of the archive. Bind the root into the AES-GCM AAD, the audit chain, and
the Solana anchor and every downstream proof inherits tamper-evidence.

Construction (domain-separated, RFC-6962 style — resists second-preimage and
forgery via promotion of lone nodes instead of duplication):

    leaf_i = SHA256( 0x00
                     || uvarint(len(name))  || name_utf8
                     || uvarint(len(zone))  || zone_json
                     || uvarint(len(payld)) || payload_i )
    node   = SHA256( 0x01 || left || right )
    root   = Merkle root over leaves sorted by column name (a lone node is
             promoted unchanged, NOT duplicated).

`payload_i` is the canonical serialization of the column's values (compact,
sorted object keys, NaN/Inf rejected) so the commitment pins the *logical*
column and is independent of the compression codec version.

`zone_json` is the EXACT zone map ({min,max,type,count,nulls}) — exact, never
noised — so range predicates evaluated against the committed zone are sound.
When a column is all-null or holds mutually non-orderable values, `min`/`max`
are OMITTED, not faked: their ABSENCE means "no sound bound is provable," so a
predicate verifier MUST fail closed (treat the bound as unprovable) and never
read a missing `min`/`max` as satisfied. See `_zone_for` for the full invariant.

Pure stdlib (hashlib + json). No third-party deps, no agent/Solana imports —
this lives in the MIT engine.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "Commitment",
    "ColumnLeaf",
    "InclusionProof",
    "commit_records",
    "commit_jsonl",
    "inclusion_proof",
    "verify_inclusion",
    "leaf_hash",
    "verify_disclosure",
]

_LEAF_TAG = b"\x00"
_NODE_TAG = b"\x01"


# --------------------------------------------------------------------------- #
# Low-level encoding helpers
# --------------------------------------------------------------------------- #
def _uvarint(n: int) -> bytes:
    """Unsigned LEB128 varint — length-prefix that can't be confused with data."""
    if n < 0:
        raise ValueError("uvarint is unsigned")
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _lp(b: bytes) -> bytes:
    """Length-prefixed field: uvarint(len) || bytes."""
    return _uvarint(len(b)) + b


def _canon(obj: Any) -> bytes:
    """Canonical JSON: sorted object keys, compact, UTF-8, NaN/Inf rejected."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def _sha256(*chunks: bytes) -> bytes:
    h = hashlib.sha256()
    for c in chunks:
        h.update(c)
    return h.digest()


def leaf_hash(name: str, zone: dict, values: list) -> bytes:
    """Deterministic 32-byte leaf for one column. A verifier recomputes this
    from a disclosed column and checks it against the committed root."""
    name_b = name.encode("utf-8")
    zone_b = _canon(zone)
    payload_b = _canon(values)
    return _sha256(_LEAF_TAG, _lp(name_b), _lp(zone_b), _lp(payload_b))


def _node_hash(left: bytes, right: bytes) -> bytes:
    return _sha256(_NODE_TAG, left, right)


# --------------------------------------------------------------------------- #
# Column / zone-map derivation (mirrors the columnar codec's logic)
# --------------------------------------------------------------------------- #
def _zone_for(values: list) -> dict:
    """EXACT zone map: {type, count, nulls, min, max}. min/max omitted when the
    column is all-null or values are not mutually orderable.

    SOUNDNESS INVARIANT (fail-closed bounds) — read before writing any predicate
    verifier against the committed zone:

        The PRESENCE of "min"/"max" asserts a sound, exact bound on the column.
        Their ABSENCE asserts NOTHING: it means "no sound bound is provable for
        this column" — NOT "the column is unbounded," NOT "every bound holds."

        A range/bound predicate (e.g. "zone.max <= cap") MUST therefore FAIL
        CLOSED when "min"/"max" is absent — treat the predicate as UNPROVABLE
        (reject / cannot-certify), NEVER as trivially satisfied. A naive verifier
        that reads a missing "max" as "no upper bound to violate, so pass" is
        exploitable: an adversary injects ONE mixed-type value, _zone_for drops
        min/max, and the bogus bound sails through. Key absence here is an
        adversary-controllable signal — never accept it as proof of a bound.
    """
    non_null = [v for v in values if v is not None]
    zone: dict = {
        "type": type(non_null[0]).__name__ if non_null else "null",
        "count": len(values),
        "nulls": len(values) - len(non_null),
    }
    if non_null:
        try:
            zone["min"] = min(non_null)
            zone["max"] = max(non_null)
        except TypeError:
            # Mixed, non-orderable types — no sound min/max exists, so we commit
            # NEITHER key (mirrors the codec's `except: pass`). Per the soundness
            # invariant above, downstream predicate verifiers MUST treat this
            # absence as UNPROVABLE and fail closed, never as satisfied.
            pass
    return zone


def _columns_from_records(records: list[dict]) -> list[tuple[str, list]]:
    """Column-major projection. Column order = first-seen key order; the Merkle
    layer re-sorts by name so order here does not affect the root."""
    if not isinstance(records, list):
        raise TypeError("records must be a list of dicts")
    key_order: list[str] = []
    seen: set[str] = set()
    for row in records:
        if not isinstance(row, dict):
            raise TypeError("every record must be a dict")
        for k in row.keys():
            if k not in seen:
                seen.add(k)
                key_order.append(k)
    n = len(records)
    cols: list[tuple[str, list]] = []
    for k in key_order:
        cols.append((k, [row.get(k) for row in records]))
    return cols


# --------------------------------------------------------------------------- #
# Public data structures
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ColumnLeaf:
    name: str
    zone: dict
    leaf: bytes  # 32-byte leaf hash

    def hex(self) -> str:
        return self.leaf.hex()


@dataclass(frozen=True)
class InclusionProof:
    name: str
    index: int                       # leaf position in the name-sorted layer
    leaf_count: int
    # ordered bottom->top siblings; each (sibling_is_right, hash)
    siblings: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "index": self.index,
            "leaf_count": self.leaf_count,
            "siblings": [[r, h.hex()] for (r, h) in self.siblings],
        }

    @staticmethod
    def from_dict(d: dict) -> "InclusionProof":
        return InclusionProof(
            name=d["name"],
            index=d["index"],
            leaf_count=d["leaf_count"],
            siblings=[(bool(r), bytes.fromhex(h)) for r, h in d["siblings"]],
        )


@dataclass(frozen=True)
class Commitment:
    root: bytes
    leaves: list  # list[ColumnLeaf], sorted by name

    def root_hex(self) -> str:
        return self.root.hex()

    def column_names(self) -> list[str]:
        return [lf.name for lf in self.leaves]

    def to_dict(self) -> dict:
        return {
            "root": self.root.hex(),
            "leaves": [{"name": lf.name, "zone": lf.zone, "leaf": lf.leaf.hex()} for lf in self.leaves],
        }


# --------------------------------------------------------------------------- #
# Merkle core
# --------------------------------------------------------------------------- #
def _build_layers(leaves: list[bytes]) -> list[list[bytes]]:
    """Bottom-up layers. Lone node promoted unchanged (no duplication)."""
    if not leaves:
        # Empty commitment: well-defined sentinel root.
        return [[_sha256(_LEAF_TAG)]]
    layers = [list(leaves)]
    cur = layers[0]
    while len(cur) > 1:
        nxt: list[bytes] = []
        for i in range(0, len(cur), 2):
            if i + 1 < len(cur):
                nxt.append(_node_hash(cur[i], cur[i + 1]))
            else:
                nxt.append(cur[i])  # promote
        layers.append(nxt)
        cur = nxt
    return layers


def _proof_for(layers: list[list[bytes]], index: int) -> list:
    siblings = []
    idx = index
    for level in layers[:-1]:
        sib = idx ^ 1
        if sib < len(level):
            sibling_is_right = (idx % 2 == 0)
            siblings.append((sibling_is_right, level[sib]))
        # else: lone node promoted — no sibling at this level
        idx //= 2
    return siblings


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def commit_records(records: list[dict]) -> Commitment:
    """Commit a list-of-dict dataset. Returns the root + per-column leaves."""
    cols = _columns_from_records(records)
    leaves_meta = []
    for name, values in cols:
        zone = _zone_for(values)
        leaves_meta.append(ColumnLeaf(name=name, zone=zone, leaf=leaf_hash(name, zone, values)))
    # Deterministic order: sort by column name.
    leaves_meta.sort(key=lambda lf: lf.name)
    layers = _build_layers([lf.leaf for lf in leaves_meta])
    return Commitment(root=layers[-1][0], leaves=leaves_meta)


def commit_jsonl(data: bytes | str) -> Commitment:
    """Commit raw JSONL bytes (one JSON object per non-empty line)."""
    if isinstance(data, bytes):
        text = data.decode("utf-8")
    else:
        text = data
    records = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        records.append(json.loads(line))
    return commit_records(records)


def inclusion_proof(commitment: Commitment, column_name: str) -> InclusionProof:
    """Produce an inclusion proof for one column."""
    names = commitment.column_names()
    try:
        index = names.index(column_name)
    except ValueError:
        raise KeyError(f"column {column_name!r} not in commitment") from None
    layers = _build_layers([lf.leaf for lf in commitment.leaves])
    return InclusionProof(
        name=column_name,
        index=index,
        leaf_count=len(commitment.leaves),
        siblings=_proof_for(layers, index),
    )


def verify_inclusion(root: bytes, leaf: bytes, proof: InclusionProof) -> bool:
    """Verify a leaf hash is committed under `root` via `proof`. Pure + cheap."""
    h = leaf
    for sibling_is_right, sib in proof.siblings:
        h = _node_hash(h, sib) if sibling_is_right else _node_hash(sib, h)
    return hmac.compare_digest(h, root)


def verify_disclosure(
    root: bytes, name: str, zone: dict, values: list, proof: InclusionProof
) -> bool:
    """The headline check: a verifier is GIVEN one column (name, zone, values)
    plus a proof, and confirms it is committed under `root` — learning nothing
    about the other columns. Returns False on any mismatch."""
    if proof.name != name:
        return False
    # Soundness: the zone MUST be the canonical map for these values. Without this,
    # a malicious committer could bind a lying min/max into a self-consistent leaf,
    # making range predicates over the "committed" zone unsound (the leaf binds
    # whatever zone the committer chose). Recompute and compare.
    if zone != _zone_for(values):
        return False
    return verify_inclusion(root, leaf_hash(name, zone, values), proof)
