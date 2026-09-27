FROM python:3.12.10-slim-bookworm@sha256:fd95fa221297a88e1cf49c55ec1828edd7c5a428187e67b5d1805692d11588db
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HOME=/models \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false
WORKDIR /app
COPY ai-service/requirements.txt ./
COPY deploy/requirements.production.txt ./requirements.production.txt
RUN pip install --no-cache-dir -r requirements.production.txt -r requirements.txt && pip check
COPY ai-service/app/ app/
COPY ai-service/migrations/ migrations/
COPY ai-service/scripts/migrate_agent_postgres.py scripts/migrate_agent_postgres.py
COPY deploy/*.py deploy/
COPY deploy/frozen-artifacts.json deploy/frozen-artifacts.json
COPY deploy/model-cache-manifest.json deploy/model-cache-manifest.json
# Generate transport-only configuration inside the image, preserving source YAML.
RUN python -m deploy.runtime configure \
    && useradd --system --uid 10001 app && chown -R app:app /app
USER app
EXPOSE 8001
ENTRYPOINT ["python", "-m", "deploy.runtime"]
CMD ["serve"]
HEALTHCHECK --interval=30s --timeout=20s --start-period=180s --retries=5 CMD python -m deploy.runtime health
