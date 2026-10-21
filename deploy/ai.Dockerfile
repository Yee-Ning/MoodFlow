FROM python:3.12.10-slim-bookworm@sha256:fd95fa221297a88e1cf49c55ec1828edd7c5a428187e67b5d1805692d11588db AS dependencies
WORKDIR /build
COPY deploy/fetch_torch_wheel.py ./
RUN python fetch_torch_wheel.py --output-dir /tmp/wheelhouse --report /tmp/wheel.json \
    && python -m pip --isolated install --no-cache-dir --no-deps /tmp/wheelhouse/*.whl
COPY ai-service/requirements.lock.txt ./
RUN python -m pip --isolated install --no-cache-dir -r requirements.lock.txt \
    && python -m pip --isolated install --no-cache-dir redis==5.2.1 \
    && python -m pip check

FROM python:3.12.10-slim-bookworm@sha256:fd95fa221297a88e1cf49c55ec1828edd7c5a428187e67b5d1805692d11588db
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HOME=/models \
    TOKENIZERS_PARALLELISM=false PYTHONPATH=/workspace:/workspace/ai-service
COPY --from=dependencies /usr/local /usr/local
WORKDIR /workspace/ai-service
COPY ai-service/app/ app/
COPY ai-service/artifacts/intent_model/ artifacts/intent_model/
COPY ai-service/data/intent/ data/intent/
COPY ai-service/data/knowledge/releases/worldline-final-native-release-20260927/v2/ data/knowledge/releases/worldline-final-native-release-20260927/v2/
COPY ai-service/migrations/ migrations/
COPY deploy/local_runtime.py /workspace/deploy/local_runtime.py
COPY scripts/setup.py /workspace/scripts/setup.py
COPY ai-service/scripts/worldline_environment_readiness.py /workspace/scripts/worldline_environment_readiness.py
RUN useradd --system --uid 10001 app \
    && mkdir -p /models data/local \
    && chown -R app:app /workspace /models
USER app
RUN python -m deploy.local_runtime verify-models
EXPOSE 8001
ENTRYPOINT ["python", "-m", "deploy.local_runtime"]
CMD ["serve"]
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=10 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health',timeout=3)"
