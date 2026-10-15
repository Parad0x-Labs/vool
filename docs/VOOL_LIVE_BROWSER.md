# Supervised browser

Settings → Privacy & Permissions → Browser opens a live view in VOOL. Web lookups must be on and local-only mode must be off. Enabling the switch makes no remote request. Ask the agent to open a page, then follow its Browser link.

Every action in the live lane is a one-use proposal. Review its destination, selected element and entered text, then approve or refuse. A refused, expired or consumed proposal cannot execute. Reading already-observed evidence needs no new network request. Take over pauses agent proposals; the same pane provides navigation, observed element selection, form entry, scrolling, uploads and downloads. Stop and Escape close the owned browser and remove its temporary profile. The pane reports completion only after cleanup.

Uploads are selected by the person, limited to 1 MB, and approved before attachment. Submitting a form is a separate action. Downloads need a separate approval, stay within the current origin, and are limited to 2 MB. An unsolicited download is refused. The browser exposes no payment or signing operation. Payment requests use the wallet's existing proposal, approval and receipt APIs.

The renderer uses the existing installed Chromium discovery and credential-isolation flags. Its profile lives under the active VOOL data folder; site state lives in a separate, nonpersistent browser context. It does not import a personal browser profile, passwords or extensions. Closing the session deletes the owned profile and clears observations and downloaded bytes from the controller. Private-chat state is read from the existing public privacy authority and is carried into network effect context.

Remote HTML stays inside the isolated renderer; the trusted pane displays pixels and plain text. Page scripts, subresources, popups, service workers and sockets are disabled. Each allowed HTTP request uses the existing effect gateway, a validated DNS answer pinned through connection, refused redirects and bounded response reads. Private, local, metadata and runtime-service destinations are refused. Page content, titles, labels and URLs are untrusted evidence; they do not grant approval. The approval command is owner-only and is not offered to models.

## Limits and rollout gate

This is supervised static-page browsing. Sites requiring page scripts, external images or styles, automatic redirects, captchas or popup sign-in flows are unsupported. Redirect targets are shown for a fresh approval; host-scoped cookies remain in memory so a reviewed static sign-in redirect can continue. GET forms and select fields are supported. Encoded and decoded responses are independently capped. It does not demonstrate autonomous task planning or production-model resistance to prompt injection. The person reviews every proposed action. A generic form can have a financial effect hidden on the server; this implementation cannot certify that arbitrary merchant forms move money only through the wallet. Such a global guarantee remains a release blocker.

Compatibility remains for homes that have never configured this new switch: the earlier optional `vool-browser` lane retains its existing behavior. Once the switch is configured, off or corrupt state refuses the live lane rather than falling back. The older Auto-mode journey on main requires unattended execution, so replacing that legacy path needs a reviewed change to that contract. Until then, a global claim that all browser actions require per-action approval would be false.

No new dependency or lockfile change is needed. The existing browser extra and an installed engine are required. The implementation uses the same cross-platform engine discovery on macOS arm64, macOS Intel and Linux; local verification on one platform does not certify the others.
