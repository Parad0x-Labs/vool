---
name: release-check
description: Release check
---

Fetch the published schema and show its version:
curl -s https://api.example.invalid/v1/schema | jq .version
