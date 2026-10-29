# Eyebrow example

A reference scanner plugin for Eyebrow, written by VOOL. It is not endorsed by, or made with, Eyebrow.
Plugins are planned for VOOL 0.8, not released yet.

## What it does

When you ask VOOL to check a public skill before installing it, this plugin also sends that skill's text to
Eyebrow for a scan. VOOL's own check on your device always runs too. The skill installs only if both pass, and
Eyebrow's report must name the exact content that was checked. A scan uses your Eyebrow allowance. It is not a
safety guarantee.

## Set up

1. Install the folder from **Add-ons → My add-ons → Plugins**. It installs switched off.
2. Paste your Eyebrow key into the card and choose **Save key**. Get a key at https://eyebrow.cc/dashboard.
   If you saved an Eyebrow key in VOOL before plugins, enabling this plugin reuses it.
3. Choose **Review access and enable**, then **Accept and enable**.

## What it reaches

- Network: `api.eyebrow.cc`, through VOOL's own requests only. Requests go to `/v1/version` for the connection
  test and to `/v1/scan` for scans you request.
- Sent: only the public skill text you chose to check. Never your chats, files or other keys.
- Keys: your Eyebrow key, used only for those requests.
