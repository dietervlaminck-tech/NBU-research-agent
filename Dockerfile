FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV NBU_DATA_DIR=/app/data
EXPOSE 8000

# Web process (default). For the durable task queue, run a SECOND container
# from this same image with the worker command instead:
#   celery -A nbu_research.worker worker --loglevel=info --concurrency=2
# (requires CELERY_BROKER_URL pointing at a shared Redis, e.g. Azure Cache.)
CMD ["./startup.sh"]
