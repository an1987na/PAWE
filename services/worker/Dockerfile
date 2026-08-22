FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY apps/api ./apps/api
COPY services/worker ./services/worker
COPY scripts ./scripts
RUN pip install --no-cache-dir .
CMD ["python", "-m", "pawe_worker.main"]
