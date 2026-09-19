FROM python:3.12-slim AS build

LABEL maintainer="Parad0x Labs"
LABEL description="VOOL — local-first AI agent runtime"
LABEL org.opencontainers.image.source="https://github.com/Parad0x-Labs/vool"

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libffi-dev curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /src

COPY . .

RUN python -m pip install --no-cache-dir --upgrade pip build && \
    python -m build --wheel

FROM python:3.12-slim AS runtime

LABEL maintainer="Parad0x Labs"
LABEL description="VOOL — local-first AI agent runtime"
LABEL org.opencontainers.image.source="https://github.com/Parad0x-Labs/vool"

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libffi-dev curl && \
    rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin vool

WORKDIR /app

COPY requirements-runtime.txt ./
COPY --from=build /src/dist /tmp/dist

RUN python -m pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements-runtime.txt && \
    pip install --no-cache-dir /tmp/dist/*.whl && \
    rm -rf /tmp/dist

ENV PYTHONUNBUFFERED=1
ENV VOOL_HOME=/data

RUN mkdir -p /data && chown -R vool:vool /app /data

USER vool

EXPOSE 8765
EXPOSE 11435

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -sf http://localhost:11435/healthz || exit 1

# Default: run the agent API server
CMD ["python3", "-m", "apps.vool_api_server"]
