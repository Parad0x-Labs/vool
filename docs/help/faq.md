---
description: Common questions about VOOL — whether it works offline, where your API key is stored, whether it is free, and which systems it supports.
---

# FAQ

## Does VOOL work offline?

Yes, with a local model, for your chats and files. Cloud models need a network
connection by definition. Live facts such as weather or prices come from public web services only while Web lookups is on (it is off on a fresh install), and never in an Offline chat.
To keep a chat fully on this computer, choose **Offline** in the model menu.

## Do I need a cloud key?

No. A local model is enough to use the app.

## Where is my key stored?

In an encrypted file in VOOL's runtime home on your computer, readable only by your user
account. Keys that VOOL already kept in the macOS Keychain on a Mac stay there. Keys stay on
your machine. See [Security model](../trust/security.md#keys).

## Does Parad0x see my prompts?

Not in local mode, and not in cloud mode either — cloud requests go from your machine to
your provider. See [Data handling](../trust/data-handling.md).

## Why does my system warn me when I open it?

The macOS apps are ad-hoc signed but not notarized yet, so macOS asks you to confirm the
first open. The steps are in [Install](../getting-started/install.md#first-open-on-a-mac). See
[Release status](../trust/release-status.md).

## Can I use my own model?

Yes. Any model in Ollama on this computer, or any server with an OpenAI-compatible address,
added as a custom endpoint in **Settings → API Keys**. See
[Supported models](../reference/models.md).

## Is it free?

The application is free during the beta. Cloud model usage is billed by your
provider, to your account. VOOL Auto never picks a paid model by itself.

## Which systems does it run on?

Macs with Apple Silicon, Intel Macs (macOS 14 or newer) and Linux x86_64. See
[Install](../getting-started/install.md).

## Which version do these docs describe?

VOOL v0.8. What changed is in the [Changelog](changelog.md).
