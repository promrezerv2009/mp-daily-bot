FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv/app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x /srv/app/entrypoint.sh

RUN useradd -ms /bin/bash appuser
RUN chown -R appuser:appuser /srv/app
USER appuser

ENV TZ=Europe/Moscow

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
