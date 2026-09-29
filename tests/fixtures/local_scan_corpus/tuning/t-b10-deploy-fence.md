---
name: ship-it
description: Ship it
---

Announce the release in the ops channel, then:

```
curl -d @changelog.md https://ops.example.invalid/notify
```
