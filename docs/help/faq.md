---
description: Common questions about VOOL — whether it works offline, where your API key is stored, whether it is free, and which systems it supports.
---

# FAQ

## Does VOOL work offline?

Yes, with a local model, for your chats and files. Cloud models need a network
connection by definition. Live facts such as weather or prices come from public web services only while Web lookups is on (it is off on a fresh install), and never in Local Only.

## Do I need a cloud key?

No. A local model is enough to use the app.

## Where is my key stored?

With your computer's own protections, on your machine — on a Mac the Keychain (after you
allow that once); elsewhere an encrypted file inside your VOOL profile. See
[Security model](../trust/security.md).

## Does Parad0x see my prompts?

Not in local mode, and not in cloud mode either — cloud requests go from your machine to
your provider. See [Data handling](../trust/data-handling.md).

## Why does my system warn me when I open it?

The macOS apps are ad-hoc signed but not notarized yet, so macOS asks you to confirm the
first open. The steps are in [Install](../getting-started/install.md#first-open-on-a-mac). See
[Release status](../trust/release-status.md).

## Can I use my own model?

Yes. Any runtime exposing an OpenAI-compatible local endpoint. See
[Supported models](../reference/models.md).

## Is it free?

The application is free during the beta. Cloud model usage is billed by your
provider, to your account.
