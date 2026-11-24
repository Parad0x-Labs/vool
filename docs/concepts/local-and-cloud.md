---
description: What data leaves your computer when you use VOOL, and when. The difference between running AI locally and using a cloud model, and the three run modes of VOOL Auto.
---

# Local and cloud

VOOL can answer in two ways: on this computer, or through a cloud provider. The model button
in the chat header shows which way the next request runs. Its small tag reads **AUTO**,
**LOCAL** or **CLOUD**, and under VOOL Auto it names the run mode.

## Local

The model and your files stay on this computer. The model runs against a local runtime, files
are read from disk, and no request is made to a provider. Live facts such as weather
or prices come from public web services only while **Web lookups** is on (it is off on a fresh
install), and never in an Offline chat.

## Cloud

The selected context is sent directly to the provider you selected, from your machine,
using your key.

"The selected context" means what the app assembled for that request: your prompt, plus
the files and prior turns the request needs.

{% hint style="danger" %}
Cloud mode sends data off your machine. That is the point of cloud mode. VOOL's claim is
about the *route* — the request goes from your computer to your provider — not that
nothing is transmitted.
{% endhint %}

Before files or remembered information first go to a cloud model, VOOL can show you the
whole request and let you trim it. See [See what leaves your computer](../guides/what-leaves-your-computer.md).

## The three run modes of VOOL Auto

Open the model menu in the chat header. Under **Where VOOL can run** you choose one of three
modes for the chat:

| Mode | What it allows |
| --- | --- |
| **Local first** | Starts on this computer. If the local model cannot handle your message and a cloud key is connected, Auto may use the free backup you chose. Auto never picks a paid model. |
| **Computer + web** | No cloud AI at all. Local models and VOOL's own tools only; web lookups still work while **Web lookups** is on. |
| **Offline** | Nothing leaves this computer: no cloud AI and no web lookups. |

Choosing an exact model in the same menu pins it for the chat instead.

If this computer is set to keep everything on this machine, VOOL stays offline in every mode,
and Settings says so.

## What sits between

Nothing operated by Parad0x Labs. There is no proxy, gateway, or relay for model traffic.
Your key is used by the app on your machine to talk to the provider directly.

Full detail: [Data handling](../trust/data-handling.md).
