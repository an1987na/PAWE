FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY apps/api ./apps/api
COPY services/worker ./services/worker
COPY scripts ./scripts
COPY alembic.ini ./alembic.ini
COPY migrations ./migrations
ENV PYTHONPATH=/app
RUN pip install --no-cache-dir .
CMD ["uvicorn", "pawe_api.main:app", "--app-dir", "apps/api", "--host", "0.0.0.0", "--port", "8000"]
