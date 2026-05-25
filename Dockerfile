FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml .
COPY src/ src/

# Install with postgres extras so asyncpg is available for production DATABASE_URL
RUN uv pip install --system -e ".[postgres]"

EXPOSE 8000

CMD ["uvicorn", "registry.main:app", "--host", "0.0.0.0", "--port", "8000"]
