# Adaptive LLM Router - backend
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first so an application edit does not reinstall the world.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
# A copy of the config ships in the image so the container starts even without
# a bind mount. docker-compose.yml mounts backend/config over this, which is
# what makes a YAML edit on the host take effect after POST /api/models/reload.
COPY config ./config

# The database lives on a mounted volume; create the mount point so a fresh
# checkout with an empty ./data still starts.
RUN mkdir -p /app/data

ENV DATABASE_URL=sqlite:////app/data/router.db \
    CONFIG_DIR=/app/config \
    PI_BRIDGE_URL=http://host.docker.internal:31415

EXPOSE 8000

HEALTHCHECK --interval=20s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
