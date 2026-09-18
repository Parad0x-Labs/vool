# Vool Webhook Ingress — Minimal Reverse Proxy Auth Notes

`webhook_ingress` is intentionally simple and should **not** be exposed raw to the public internet.

Recommended model:

- keep `webhook_ingress` bound to `127.0.0.1:8989`
- put a reverse proxy in front of it
- require either:
  - a shared secret header, or
  - basic auth, or
  - source IP allowlist
- optionally rate-limit requests at the proxy

---

## 1) Safe baseline

Run locally:

- `relay/http_mirror_server.py` on `127.0.0.1:8787`
- `relay/bridge_workers/webhook_ingress.py` on `127.0.0.1:8989`

Do **not** bind these directly to `0.0.0.0` unless the machine is already protected.

---

## 2) Nginx example (shared secret header)

This pattern requires a custom header from your bot infrastructure.

```nginx
server {
    listen 443 ssl http2;
    server_name your-domain.example;

    # SSL config omitted for brevity

    location / {
        if ($http_x_vool_bridge_secret != "CHANGE_ME_LONG_RANDOM_SECRET") {
            return 403;
        }

        proxy_pass http://127.0.0.1:8989;
        proxy_http_version 1.1;

        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        client_max_body_size 256k;
        limit_except POST GET { deny all; }
    }
}
```

Notes:
- Rotate the secret if it leaks.
- Keep request bodies small.
- Only allow the methods you actually use.

## 3) Nginx example (basic auth)

If your bot client can send basic auth, this is easy.

```nginx
server {
    listen 443 ssl http2;
    server_name your-domain.example;

    # SSL config omitted for brevity

    location / {
        auth_basic "Vool Bridge";
        auth_basic_user_file /etc/nginx/.vool_htpasswd;

        proxy_pass http://127.0.0.1:8989;
        proxy_http_version 1.1;

        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;

        client_max_body_size 256k;
        limit_except POST GET { deny all; }
    }
}
```

## 4) Caddy example (basic auth)
```caddy
your-domain.example {
    basicauth {
        bridgeuser $2a$14$REPLACE_WITH_BCRYPT_HASH
    }

    reverse_proxy 127.0.0.1:8989
}
```

## 5) IP allowlist pattern

If your bots run from fixed egress IPs, combine auth with an allowlist.

```nginx
location / {
    allow 203.0.113.10;
    allow 203.0.113.11;
    deny all;

    proxy_pass http://127.0.0.1:8989;
}
```
This is good as a second layer, not the only layer.

## 6) Recommended request hardening

At the proxy layer:

- restrict to GET and POST
- cap body size to 256 KB
- add simple request rate limits
- log upstream status codes
- reject obviously invalid content types if desired

```nginx
limit_req_zone $binary_remote_addr zone=vool_ingress:10m rate=10r/s;

server {
    listen 443 ssl http2;
    server_name your-domain.example;

    location / {
        limit_req zone=vool_ingress burst=20 nodelay;

        proxy_pass http://127.0.0.1:8989;
    }
}
```

## 7) Operational best practice

Treat the public proxy as:
- transport/auth/rate-limit boundary

Treat webhook_ingress as:
- internal app service

Treat http_mirror_server as:
- internal mirror store

**Do not let the reverse proxy talk directly to the mirror unless that is intentional.**

Preferred chain:
`external bot -> reverse proxy -> webhook_ingress -> http_mirror_server`

## 8) Secret handling

For shared-secret mode:
- use a long random secret
- store it in proxy config via environment or secret manager if possible
- do not hardcode it in bot source if avoidable
- rotate on suspicion of compromise

## 9) TLS

Always terminate TLS at the reverse proxy if traffic leaves localhost.

Even if the ingress is simple, encrypted transit still matters for:
- bridge secrets
- offer payloads
- snapshot payloads

## 10) v1 recommendation

Best minimal safe starting point:
- bind mirror + ingress to localhost
- put Nginx or Caddy in front
- require either shared secret header or basic auth
- add small body cap
- add rate limiting
- optionally restrict source IPs

That is enough for a solid v1.
