ARG PYTHON_BASE_IMAGE
FROM ${PYTHON_BASE_IMAGE}

ARG CRYPTOGRAPHY_VERSION
RUN test -n "${CRYPTOGRAPHY_VERSION}" \
    && python -m pip install --no-cache-dir --disable-pip-version-check "cryptography==${CRYPTOGRAPHY_VERSION}" \
    && python -m pip check

WORKDIR /app
COPY portable/ /app/portable/
COPY scripts/ /app/scripts/

RUN groupadd --gid 10001 auren \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin auren \
    && mkdir -p /var/lib/auren \
    && chown -R 10001:10001 /var/lib/auren

ENV PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER 10001:10001
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=2).read()" || exit 1
CMD ["python", "-m", "scripts.evidence_archive_server"]
