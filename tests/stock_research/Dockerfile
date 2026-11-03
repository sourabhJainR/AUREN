FROM python:3.12-slim

ARG TRADINGDYOR_SOURCE_SHA
LABEL org.opencontainers.image.revision="${TRADINGDYOR_SOURCE_SHA}" \
      org.opencontainers.image.source="https://github.com/sourabhJainR/TradingDYOR"
WORKDIR /opt/TradingDYOR
COPY external/TradingDYOR/ /opt/TradingDYOR/
RUN pip install --no-cache-dir ".[dev]" \
    && chmod -R a+rX /opt/TradingDYOR
ENV PYTHONDONTWRITEBYTECODE=1
USER 65534:65534
