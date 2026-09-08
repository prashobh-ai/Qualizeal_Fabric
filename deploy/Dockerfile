# Portable image — identical across local / interim / client (Section 17).
# The application is stdlib-only Python; concrete engines are chosen by env,
# not by the image. Non-root, minimal.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    KF_DB=/data/kf.db \
    KF_BLOBS=/data/blobs \
    KF_PORT=8080

WORKDIR /app
COPY knowledge_fabric ./knowledge_fabric
COPY scripts ./scripts
COPY tests ./tests

RUN useradd -m kf && mkdir -p /data && chown -R kf /data /app
USER kf
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/health').status==200 else 1)"

CMD ["python", "-m", "knowledge_fabric.surfaces.http_api"]
