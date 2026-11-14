FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app

RUN python -m pip install --no-cache-dir "uvicorn[standard]>=0.30,<1" \
    && mkdir -p /var/lib/auren /workspace/project \
    && chown -R 10001:10001 /var/lib/auren /workspace/project

USER 10001:10001

EXPOSE 8765
CMD ["python", "-m", "portable.remote_mcp_server"]
