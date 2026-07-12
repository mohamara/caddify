FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends curl \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV DATA_DIR=/data
ENV DASHBOARD_PORT=9090

EXPOSE 9090

CMD ["sh", "-c", "LVL=$(echo \"${LOG_LEVEL:-info}\" | tr '[:upper:]' '[:lower:]'); uvicorn app:app --host 0.0.0.0 --port 9090 --log-level \"$LVL\""]
