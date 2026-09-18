# Bring up the local SearXNG stack (SearXNG + Valkey cache) via Docker Compose IF Docker is usable.
# Docker is OPTIONAL: VOOL web search falls back to DuckDuckGo (WEB_SEARCH_PROVIDER_ORDER), so a
# missing or stopped Docker engine is graceful degradation, not a failure. Report the real state
# honestly -- exit non-zero and say so when the stack did not start, and never claim "SearXNG up"
# unless `docker compose up -d` actually succeeded.
$ErrorActionPreference = "Continue"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
  Write-Host "Docker not installed; SearXNG not started. VOOL web search uses the DuckDuckGo fallback."
  exit 1
}

# The docker binary can be present while the engine is stopped (e.g. Docker Desktop not running).
# `docker info` is the standard engine-liveness probe; it fails cleanly when the daemon is unreachable.
# Probing here means we never run `docker compose` against a dead engine -- which is what produced the
# alarming raw "failed to connect to the Docker API" error in the install log.
docker info 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
  Write-Host "Docker engine not running; SearXNG not started. VOOL web search uses the DuckDuckGo fallback."
  exit 1
}

Set-Location (Join-Path $PSScriptRoot "..\infra\searxng")
docker compose up -d
if ($LASTEXITCODE -ne 0) {
  Write-Host "docker compose could not start SearXNG. VOOL web search uses the DuckDuckGo fallback."
  exit 1
}

Write-Host "SearXNG up at http://localhost:8080 (JSON: /search?q=...&format=json)"
exit 0
