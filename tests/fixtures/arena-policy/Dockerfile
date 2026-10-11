FROM python:3.12-slim
COPY policy.py /opt/arena-policy.py
USER 65534:65534
ENTRYPOINT ["python", "/opt/arena-policy.py"]
