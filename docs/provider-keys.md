# Provider keys

Open **Settings → API Keys → Find a provider**. Choose a category or search by provider name.

- **AI models:** connect the provider that runs your cloud model. Local models need no API key.
- **Web search:** connect a search API for live lookups. Built-in keyless search remains available.
- **Security scans:** connect Eyebrow for optional external add-on checks. A scan never grants tool permissions.

Each provider has a short description and an official site link where available. Plans, prices and quotas belong to the provider; check its site before adding credit. A custom model endpoint has no shared signup site.

Choose **Add key**, paste the provider's API key, and save. VOOL verifies that selected service before storing the key sealed on this machine. Search verification may consume credits. Eyebrow verification requests version information; it does not scan or install an add-on, or change the active model.

Saved keys appear by category with **Test** and **Remove** controls. Reloading Settings does not contact providers or display your stored secret. A saved key is a connection, not permission to perform a protected action. Runtime-managed encryption keys cannot be removed here.
