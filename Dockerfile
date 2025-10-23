# STAGE 1: Build dependencies
FROM python:3.11-slim AS builder
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential libpq-dev gcc && \
    rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

# STAGE 2: Runtime
FROM python:3.11-slim
RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq5 libmagic1 poppler-utils curl tini && \
    rm -rf /var/lib/apt/lists/* && apt-get clean

RUN adduser --disabled-password --gecos '' appuser && \
    mkdir -p /app/media/pdfs && chown -R appuser:appuser /app

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

WORKDIR /app
COPY --chown=appuser:appuser . .

USER appuser
ENTRYPOINT ["tini", "--"]
EXPOSE 8000