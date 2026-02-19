FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    git && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY watcher.py .

# Non-root user
RUN useradd -m -u 1000 watcher
USER watcher

ENTRYPOINT ["python", "-u", "watcher.py"]
