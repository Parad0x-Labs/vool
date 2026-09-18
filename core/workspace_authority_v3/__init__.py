"""VOOL workspace mutation authority V3 dormant Phase-0 foundation.

The package contains schemas, pure state machines, authenticated codecs, and a
non-authorizing database. It is intentionally disconnected from live workspace tools,
legacy rollback, Activity, and Changes projection code.

Trust boundary
--------------

The closure-held owners in this package (identity, key authority, workspace state,
serialization) defend the authority object graph against ordinary manipulation:
subclassing, replacing instance/class attributes and properties, mutating frozen fields,
replacing public module wrappers, and the construction bypasses named in the contract.
They also enforce construction-to-validation consistency, so a record cannot be built
under one encoding and later accepted under another.

They do not defend against arbitrary code substitution inside the same Python
interpreter — closure-cell rewriting, ``__code__``/``__globals__`` patching, or swapping
stdlib primitives. No pure-Python arrangement can, and Phase-0 makes no such claim.
``base.py`` carries the full statement of this boundary.

Authority-domain provenance
---------------------------

Every logical key ring is anchored to exactly one authority domain, and an authenticated
identity decode issues owned identities only when the record's ``authority_domain_id``
matches the verifying ring's anchor. A key authority therefore cannot authenticate a
record naming a domain it is not entitled to, however self-consistent that record is.

What this does not decide is which process is the genuine authority for a given domain.
Anchoring by owned ``AuthorityDomainId`` roots the ring in a real
``AuthorityClaimProvenance``; anchoring by stored text does not, and exists because a
restart must reconstruct the anchor without re-minting the very identity whose decode it
authorizes. In Phase-0 there is no persistent trust root, so a process that anchors a new
ring to a domain's text is asserting to be that domain's authority — exactly as strong as
its existing assertion that it holds that domain's key material, and no stronger. Records
still do not cross rings: the ring context bound into every MAC keeps one ring's
authenticated records unverifiable by any other.

Integration note for the Phase-1 lane (not implemented here)
------------------------------------------------------------

Before Mutation Authority becomes executable, trusted authority code must not share an
agent-writable runtime/import root without an enforced boundary. While the agent runtime
can write the same source tree it imports this package from, in-process code substitution
is reachable in normal operation rather than only under an assumed compromise, and no
amount of additional in-language sealing closes it. Separating the trusted authority
runtime source from agent-writable workspace content is a Phase-1/integration concern and
is deliberately out of scope for Phase-0 repair work.
"""

from core.workspace_authority_v3.base import AuthorityPhase, AuthorityRecord, ContractValidationError
from core.workspace_authority_v3.canonical import *  # noqa: F403
from core.workspace_authority_v3.canonical import __all__ as _canonical_exports
from core.workspace_authority_v3.contracts import *  # noqa: F403
from core.workspace_authority_v3.contracts import __all__ as _contract_exports
from core.workspace_authority_v3.cursor import *  # noqa: F403
from core.workspace_authority_v3.cursor import __all__ as _cursor_exports
from core.workspace_authority_v3.database import *  # noqa: F403
from core.workspace_authority_v3.database import __all__ as _database_exports
from core.workspace_authority_v3.identity import *  # noqa: F403
from core.workspace_authority_v3.identity import __all__ as _identity_exports
from core.workspace_authority_v3.lifecycle import *  # noqa: F403
from core.workspace_authority_v3.lifecycle import __all__ as _lifecycle_exports
from core.workspace_authority_v3.path_policy import *  # noqa: F403
from core.workspace_authority_v3.path_policy import __all__ as _path_policy_exports

PHASE = AuthorityPhase.DORMANT_PHASE0
EXECUTION_AUTHORITY = False

__all__ = [
    "EXECUTION_AUTHORITY",
    "PHASE",
    "AuthorityPhase",
    "AuthorityRecord",
    "ContractValidationError",
    *_canonical_exports,
    *_contract_exports,
    *_cursor_exports,
    *_database_exports,
    *_identity_exports,
    *_lifecycle_exports,
    *_path_policy_exports,
]
