"""Email provider adapters: one shared contract, provider-specific transports.

The owner's PA scope requires the accounts people actually use — Gmail/Google
Workspace (Gmail API + OAuth), Outlook.com/Microsoft365 (Graph + OAuth), iCloud
Mail (documented IMAP/SMTP) — with the same reviewable-draft/approval/receipt
law the standards path has. KAS owns external connector integration; VOOL owns
permission, user intent, task state and truthful delivery (docs/KAS_BOUNDARY.md,
docs/TOOL_PERMISSION_AUTHORITY.md). No separate KAS repository exists at this
time, so per the mission contract these adapters ship HERE as executable
KAS-boundary source (transport only, no policy) behind a VOOL-owned seam:

  * the SEAM is `core.email_tools`: every tool intent (email.read/open/draft.*)
    keeps its single permission classification, approval binding and receipt
    store; an account's credential blob names its provider and the transport is
    delegated — there is no second registry, no second assistant, and no
    provider-specific permission surface;
  * OAuth tokens are credential-store HANDLES (`email.oauth.<provider>.<account>`):
    refresh happens inside the adapter, tokens never surface in chat, and a
    revoked/expired grant is an honest `needs_reauthorization` — never fake
    success and never a password prompt;
  * every adapter is exercised against a LOCAL recorded-shape HTTPS-boundary
    server (real sockets, the provider's documented REST shapes). Those tests
    prove our request/response handling, NOT the live provider: live credential
    verification is an explicitly labelled acceptance gate.

Provider selection: an account blob may set `"provider": "gmail" | "graph" |
"icloud" | "imap"` (default imap). iCloud and imap use the standards path
(ssl-validated IMAP/SMTP); iCloud adds the documented host/identity setup
contract (see icloud.py) because its app-specific-password flow is a setup UX,
not a code path.
"""
from core.email_providers.base import (
    ProviderAccountError,
    ProviderCapability,
    provider_for_account,
)
from core.email_providers.gmail import GmailAdapter
from core.email_providers.graph import GraphAdapter
from core.email_providers.icloud import icloud_setup_guidance, verify_icloud_account

__all__ = [
    "GmailAdapter",
    "GraphAdapter",
    "ProviderAccountError",
    "ProviderCapability",
    "icloud_setup_guidance",
    "provider_for_account",
    "verify_icloud_account",
]
