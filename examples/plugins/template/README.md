# Text digest example

Compute the SHA-256 digest of supplied text through a bundled local connection server.

The `sha256` result is a fingerprint with colon-separated hexadecimal bytes. Removing the colons gives the ordinary 64-character digest. This preserves every byte while keeping the untrusted result distinct from an unlabelled private-key string; secret filtering remains enabled.

This is an example written by VOOL, with no endorsement from the named service. Install from this folder in Add-ons, review every file, connect your own service key if requested, then review access and enable. Installation keeps the package disabled.

The VOOL declarations use the existing `.codex-plugin/plugin.json` format. The minimal `.claude-plugin/plugin.json` supplies package metadata. Native provider and scanner integration in other hosts depends on those hosts; metadata alone does not add those capabilities.

The bundled stdio server requires Python 3 and uses its standard library. Run `bin/server` with this folder as the working directory, exactly as `.mcp.json` specifies. No package download or network connection occurs. The server offers `sha256_text`. VOOL confines it and frames its output as untrusted data.
