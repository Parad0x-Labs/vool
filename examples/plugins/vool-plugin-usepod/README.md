# UsePod example

A reference model provider plugin for UsePod, written by VOOL. It is not endorsed by, or made with, UsePod.
Plugins are planned for VOOL 0.8, not released yet.

## What it does

UsePod is paid from a balance that you fund through VOOL's wallet. So this plugin never turns UsePod into a
plain model provider, and it never hands a UsePod token to plugin code. With the plugin enabled, VOOL keeps
using your saved UsePod connection through the wallet's approvals, spending limits and receipts. Choosing
**Refresh model list** explains this and adds no models.

If you used UsePod before plugins, VOOL keeps your saved token in its own place, unchanged. Chats that used a
UsePod model have no model selected after the upgrade, and automatic cloud escalation is off until you choose
again.

## What it reaches

- Network: `api.usepod.ai`, through VOOL's own requests only.
- Keys: none given to plugin code.
- Files: none.

## As a model provider example

The manifest shows the `model_provider` fields with path authentication (`auth_path` containing
`{credential}`). For a service that is not paid from a VOOL wallet balance, the same fields make a working
provider. See [docs/authoring.md](https://github.com/Parad0x-Labs/vool-plugins/blob/main/docs/authoring.md#model-providers-and-scanners).
