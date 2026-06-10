FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV NBU_DATA_DIR=/app/data
EXPOSE 8000

CMD ["./startup.sh"]
