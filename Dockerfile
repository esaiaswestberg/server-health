FROM node:20-slim

# System deps:
#   python3/pip      - the health-check app itself
#   cron             - in-container scheduler (schedule is configurable, see entrypoint.sh)
#   docker.io        - `docker` CLI, talks to the mounted host docker.sock
#   iputils-ping     - network reachability check
#   ca-certificates  - TLS for ntfy / speedtest / codex
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        cron \
        docker.io \
        iputils-ping \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @openai/codex

WORKDIR /opt/health-check

COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

COPY app ./app
COPY schema ./schema
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["./entrypoint.sh"]
