FROM python:3.12-slim

WORKDIR /app

# Install Claude Code CLI (optional, for claude-code mode)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl git && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    npm install -g @anthropic-ai/claude-code && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY watcher.py .

# Non-root user
RUN useradd -m -u 1000 watcher
USER watcher

ENTRYPOINT ["python", "-u", "watcher.py"]
