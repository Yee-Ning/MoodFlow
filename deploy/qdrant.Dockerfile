FROM qdrant/qdrant:v1.19.0@sha256:057ee3a8da769fe7310dd3537b4dc7583bf87a95ce8ac43c0af5a46bc580d1fc
# Use upstream Bash for an actual HTTP /readyz check without adding packages.
COPY deploy/qdrant-start.sh /qdrant/moodflow-start.sh
COPY deploy/qdrant-health.sh /qdrant/moodflow-health.sh
ENTRYPOINT ["sh", "/qdrant/moodflow-start.sh"]
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 CMD bash /qdrant/moodflow-health.sh
