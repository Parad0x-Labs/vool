# Private web helper programs

VOOL's Private web setting runs two programs that are not part of VOOL's own code. They are
fetched and checked at build time by `installer/private_web_helpers.py` (pinned versions and
SHA-256 values) and placed under `third_party/tor/<platform>/` and
`third_party/wireproxy/<platform>/` in the built app. They are not stored in this repository.

| Program | Version | Licence | Source |
| --- | --- | --- | --- |
| tor (Tor Expert Bundle) | from Tor Browser 15.0.9 (tor 0.4.9.x) | BSD-3-Clause; bundled libraries keep their own licences, listed in the bundle's `docs/` | https://archive.torproject.org/tor-package-archive/torbrowser/15.0.9/ |
| wireproxy | v1.1.3 (module github.com/windtf/wireproxy) | ISC | https://github.com/windtf/wireproxy |

The Tor bundle's SHA-256 values come from the Tor Project's `sha256sums-signed-build.txt` for that
release, whose signature verifies against the Tor Browser Developers signing key
(EF6E 286D DA85 EA2A 4BA7 DE68 4E2C 6E87 9329 8290). The wireproxy values are of a reproducible
`CGO_ENABLED=0 go build -trimpath -ldflags "-s -w"` of the pinned version with go1.26.0.
