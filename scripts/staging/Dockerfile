FROM python:3.12-slim
WORKDIR /workspace
COPY *.py /workspace/
COPY portable/ /workspace/portable/
COPY tests/ /workspace/tests/
COPY scripts/staging/multi_instance_container_worker.py /workspace/scripts/staging/multi_instance_container_worker.py
RUN python -m pip install --no-cache-dir --disable-pip-version-check "psycopg[binary]>=3.2,<4"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/workspace
