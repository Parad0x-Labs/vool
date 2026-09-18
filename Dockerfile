FROM python:3.12-slim AS build

LABEL maintainer="Parad0x Labs"
LABEL description="NULLA Hive Mind — local-first decentralized AI agent"
LABEL org.opencontainers.image.source="https://github.com/Parad0x-Labs/nulla-hive-mind"

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libffi-dev curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /src

COPY . .

RUN python -m pip install --no-cache-dir --upgrade pip build && \
    python -m build --wheel

FROM python:3.12-slim AS runtime

LABEL maintainer="Parad0x Labs"
LABEL description="NULLA Hive Mind — local-first decentralized AI agent"
LABEL org.opencontainers.image.source="https://github.com/Parad0x-Labs/nulla-hive-mind"

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libffi-dev curl && \
    rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin nulla

WORKDIR /app

COPY requirements-runtime.txt ./
COPY --from=build /src/dist /tmp/dist

RUN python -m pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements-runtime.txt && \
    pip install --no-cache-dir /tmp/dist/*.whl && \
    rm -rf /tmp/dist

ENV PYTHONUNBUFFERED=1
ENV NULLA_DATA_DIR=/data

RUN mkdir -p /data && chown -R nulla:nulla /app /data

USER nulla

EXPOSE 49152/udp
EXPOSE 8765
EXPOSE 11435

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -sf http://localhost:11435/healthz || exit 1

# Default: run the agent API server
CMD ["python3", "-m", "apps.nulla_api_server"]
