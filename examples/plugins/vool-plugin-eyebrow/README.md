# Eyebrow example

Add an optional remote scan of the public add-on content you choose.

This is an example written by VOOL, with no endorsement from the named service. Install from this folder in Add-ons, review every file, connect your own service key if requested, then review access and enable. Installation keeps the package disabled.

The VOOL declarations use the existing `.codex-plugin/plugin.json` format. The minimal `.claude-plugin/plugin.json` supplies package metadata. Native provider and scanner integration in other hosts depends on those hosts; metadata alone does not add those capabilities.

Without an enabled scanner plugin, installation checks run on the device. With this plugin enabled and keyed, an explicitly requested public add-on scan also contacts its declared host. A passing report is evidence, not an installation permission or safety guarantee. Keep only one scanner enabled to make the choice explicit.
