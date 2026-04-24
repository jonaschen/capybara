# 水豚教練 (Capybara Coach) — LINE Webhook Server
#
# Build:
#   docker build -t capybara-coach .
# Local run:
#   docker run --rm --env-file .env -p 8080:8080 capybara-coach

FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Layer cache: requirements first
COPY tools/requirements.txt /app/tools/requirements.txt
RUN pip install --no-cache-dir -r tools/requirements.txt

# Copy project
COPY . .

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

EXPOSE 8080

CMD ["python3", "tools/line_webhook.py"]
