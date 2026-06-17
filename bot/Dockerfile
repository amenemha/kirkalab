# Kirkalab Telegram bot image.
# Build context is the repository root so the `bot` package is importable:
#   docker build -f bot/Dockerfile -t kirkalab-bot .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install bot dependencies first to leverage Docker layer caching.
COPY bot/requirements.txt ./bot/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r bot/requirements.txt

# Copy the bot package.
COPY bot ./bot

# Long-polling client; no inbound ports required.
CMD ["python", "-m", "bot.main"]
