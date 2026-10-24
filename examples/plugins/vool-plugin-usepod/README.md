# UsePod example

Connect your own UsePod service account through a declarative model provider.

This is an example written by VOOL, with no endorsement from the named service. Install from this folder in Add-ons, review every file, connect your own service key if requested, then review access and enable. Installation keeps the package disabled.

The VOOL declarations use the existing `.codex-plugin/plugin.json` format. The minimal `.claude-plugin/plugin.json` supplies package metadata. Native provider and scanner integration in other hosts depends on those hosts; metadata alone does not add those capabilities.

Choose Refresh model list after enabling and connecting. Path authentication is assembled at invocation; the token is not saved in model manifests. Published prices are service reports, not charge receipts; normal spending checks and conservative unknown-price handling remain. A payment challenge cannot authorize payment. The existing money lane requires separate owner controls and an enabled provider declaring x402; a provider-neutral money-lane rewrite is not released.
