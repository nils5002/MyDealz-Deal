FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY mydealz_monitor.py ./

# Default state path can be overridden via env var (see docker-compose.yml)
ENV STATE_PATH=/data/state.json

CMD ["python", "mydealz_monitor.py"]
