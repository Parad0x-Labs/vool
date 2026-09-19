---
description: Common questions about VOOL — whether it works offline, where your API key is stored, whether it is free, and which systems it supports.
---

# FAQ

## Does VOOL work offline?

Yes, with a local model. Cloud models need a network connection by definition.

## Do I need a cloud key?

No. A local model is enough to use the app.

## Where is my key stored?

In your operating system's credential store, on your machine. See
[Security model](../trust/security.md).

## Does Parad0x see my prompts?

Not in local mode, and not in cloud mode either — cloud requests go from your machine to
your provider. See [Data handling](../trust/data-handling.md).

## Why does my system warn me when I open it?

The current builds are unsigned and the macOS build is not notarized. See
[Release status](../trust/release-status.md).

## Can I use my own model?

Yes. Any runtime exposing an OpenAI-compatible local endpoint. See
[Supported models](../reference/models.md).

## Is it free?

The application is free during the beta. Cloud model usage is billed by your
provider, to your account.
