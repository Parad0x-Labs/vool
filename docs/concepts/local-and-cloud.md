---
description: What data leaves your computer when you use VOOL, and when. The difference between running AI locally and using a cloud model.
---

# Local and cloud

VOOL can answer in two ways: on this computer, or through a cloud provider. The chat
header shows which way the next request runs — for example **AUTO** with
**Local first** when VOOL Auto answers from this computer.

## Local

The model and your files stay on this computer. The model runs against a local runtime, files
are read from disk, and no request is made to a provider. Live facts such as weather
or prices come from public web services only while Web lookups is on (it is off on a fresh install), and never in Local Only.

## Cloud (provider name)

The selected context is sent directly to the provider you selected, from your machine,
using your key.

"The selected context" means what the app assembled for that request: your prompt, plus
the files and prior turns the request needs.

{% hint style="danger" %}
Cloud mode sends data off your machine. That is the point of cloud mode. VOOL's claim is
about the *route* — the request goes from your computer to your provider — not that
nothing is transmitted.
{% endhint %}

## What sits between

Nothing operated by Parad0x Labs. There is no proxy, gateway, or relay for model traffic.
Your key is used by the app on your machine to talk to the provider directly.

Full detail: [Data handling](../trust/data-handling.md).
