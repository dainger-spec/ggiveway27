# Сборка для Bothost («Использовать собственный Dockerfile») и любого Docker-хостинга.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/app/data

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Бот и веб-сервер мини-приложения в одном процессе; порт берётся из PORT, адрес — 0.0.0.0.
CMD ["python", "main.py"]
