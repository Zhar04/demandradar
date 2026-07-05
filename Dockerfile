FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt pyproject.toml ./
COPY src ./src
COPY config ./config
RUN pip install --no-cache-dir .

# конфиги и БД монтируются томами (см. docker-compose.yml)
VOLUME ["/app/data", "/app/logs"]

ENV DR_DB_PATH=/app/data/demandradar.db \
    PYTHONUNBUFFERED=1

# по умолчанию — демон 24/7; дашборд — отдельный сервис в compose
CMD ["python", "-m", "demandradar", "--daemon"]
