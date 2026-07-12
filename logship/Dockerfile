FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ship.py .

ENV LOGS_ROOT=/logs
ENV LOG_LEVEL=INFO
ENV LOG_S3_ENABLED=0
ENV LOG_S3_INTERVAL=3600

CMD ["python", "-u", "ship.py"]
