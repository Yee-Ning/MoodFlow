FROM python:3.12.10-slim-bookworm@sha256:fd95fa221297a88e1cf49c55ec1828edd7c5a428187e67b5d1805692d11588db AS dependencies
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HOME=/models \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false
WORKDIR /app
COPY ai-service/requirements.txt ./
COPY deploy/requirements.production.txt ./requirements.production.txt
COPY --from=wheelhouse /torch-2.14.0+cpu-cp312-cp312-manylinux_2_28_x86_64.whl /tmp/wheelhouse/torch-2.14.0+cpu-cp312-cp312-manylinux_2_28_x86_64.whl
RUN printf '%s  %s\n' 'a09987c95ec4cffdb6df798d3d641558110a334cfccce22f2f046d83142bc260' '/tmp/wheelhouse/torch-2.14.0+cpu-cp312-cp312-manylinux_2_28_x86_64.whl' | sha256sum -c - \
    && printf '%s\n' 'torch==2.14.0+cpu --hash=sha256:a09987c95ec4cffdb6df798d3d641558110a334cfccce22f2f046d83142bc260' > /tmp/torch-requirement.txt \
    && python -m pip --isolated install --no-cache-dir --no-index --no-deps --find-links=/tmp/wheelhouse --require-hashes -r /tmp/torch-requirement.txt \
    && python -m pip --isolated install --no-cache-dir --index-url https://pypi.org/simple -r requirements.production.txt -r requirements.txt \
    && python -m pip check \
    && rm -rf /tmp/wheelhouse /tmp/torch-requirement.txt

# The copied wheel belongs only to the dependency stage; the runtime image
# receives installed packages without carrying the wheel archive layer.
FROM python:3.12.10-slim-bookworm@sha256:fd95fa221297a88e1cf49c55ec1828edd7c5a428187e67b5d1805692d11588db
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HOME=/models \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false
WORKDIR /app
COPY --from=dependencies /usr/local /usr/local
COPY --from=dependencies /app/requirements.txt ./requirements.txt
COPY --from=dependencies /app/requirements.production.txt ./requirements.production.txt
RUN python -m pip check
COPY ai-service/app/ app/
COPY ai-service/evals/config/router_capability_contract.json evals/config/router_capability_contract.json
COPY ai-service/evals/config/jev_multi_frozen_strategy_v3.json evals/config/jev_multi_frozen_strategy_v3.json
COPY ai-service/data/intent/taxonomy.json data/intent/taxonomy.json
COPY ai-service/migrations/ migrations/
COPY ai-service/scripts/migrate_agent_postgres.py scripts/migrate_agent_postgres.py
COPY deploy/*.py deploy/
COPY deploy/frozen-artifacts.json deploy/frozen-artifacts.json
COPY deploy/model-cache-manifest.json deploy/model-cache-manifest.json
# Generate transport-only configuration inside the image, preserving source YAML.
RUN printf '%s  %s\n' 'a60027b108e744d2baa457b6221572b9720d6f1ba377ef0350dbe014002bc63a' '/app/data/intent/taxonomy.json' | sha256sum -c - \
    && python -m deploy.runtime configure \
    && useradd --system --uid 10001 app && chown -R app:app /app
USER app
EXPOSE 8001
ENTRYPOINT ["python", "-m", "deploy.runtime"]
CMD ["serve"]
HEALTHCHECK --interval=30s --timeout=20s --start-period=180s --retries=5 CMD python -m deploy.runtime health
