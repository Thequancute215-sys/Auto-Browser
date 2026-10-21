# Auto Browser in a container: headless Chromium + web UI on port 8000 ("stream" mode).
# Base image = Playwright's official image with the browsers matching playwright==1.64.0 (amd64 + arm64).
FROM mcr.microsoft.com/playwright/python:v1.64.0-noble

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000 \
    AIB_BIND=0.0.0.0 \
    AIB_IN_DOCKER=1 \
    AIB_BROWSER_MODE=stream \
    AIB_KILL_STALE=0 \
    AIB_PROFILE_DIR=/data/profile \
    AIB_AI_CONFIG=/data/ai_config.json \
    OLLAMA_HOST=http://host.docker.internal:11434

WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir --break-system-packages -r requirements.lock

COPY *.py ./
COPY static ./static

# Everything that must survive updates (logins, API keys, settings) lives in the /data volume.
RUN mkdir -p /data && chown -R pwuser:pwuser /data /app
USER pwuser
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/state', timeout=4).status == 200 else 1)"

LABEL org.opencontainers.image.title="Auto Browser" \
      org.opencontainers.image.description="Trình duyệt có trợ lý AI tự làm bài tập trực tuyến" \
      org.opencontainers.image.source="https://github.com/Thequancute215-sys/Auto-Browser"

CMD ["python3", "server.py"]
