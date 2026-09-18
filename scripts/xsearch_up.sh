#!/usr/bin/env bash
set -euo pipefail

if ! command -v docker >/dev/null 2>&1; then
  echo "docker not found; cannot start SearXNG"
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "docker compose plugin not found; cannot start SearXNG"
  exit 1
fi

cd "$(dirname "$0")/../infra/searxng"
docker compose up -d
echo "SearXNG up at http://localhost:8080 (JSON: /search?q=...&format=json)"
