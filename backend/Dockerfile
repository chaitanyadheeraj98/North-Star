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

# reference/ lives one level above this build context (./backend), so it
# cannot be COPYed the way config/ is - only an empty mount point here.
# docker-compose.yml mounts the repo's reference/ over this at runtime, which
# is what lets Update Models' research pass land its rewrites on the host's
# git-tracked ClaudeLLM.md/CodexLLM.md. Without that mount (e.g. `docker run`
# on the bare image) the directory is simply empty, and Update Models degrades
# to writing no reference docs rather than failing.
RUN mkdir -p /app/reference

ENV DATABASE_URL=sqlite:////app/data/router.db \
    CONFIG_DIR=/app/config \
    REFERENCE_DIR=/app/reference \
    PI_BRIDGE_URL=http://host.docker.internal:31415

EXPOSE 8000

HEALTHCHECK --interval=20s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
