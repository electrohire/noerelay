FROM python:3.12-slim

WORKDIR /app

ENV PYTHONPATH=/app/reference

# Copy project files
COPY reference/ ./reference/
COPY spec/ ./spec/
COPY examples/ ./examples/
COPY scripts/ ./scripts/
COPY benchmarks/ ./benchmarks/
COPY tests/ ./tests/
COPY .env.example ./.env.example

# Install pytest for testing (dev only)
RUN pip install --no-cache-dir pytest jsonschema referencing

# Expose gateway port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)"

# Start gateway
ENV NOERELAY_OPENROUTER_MODE=stub
ENV NOERELAY_DATABASE_ENABLED=1
ENV NOERELAY_DATABASE_PATH=/data/noerelay.db
ENV NOERELAY_LOG_OUTPUT=stdout

VOLUME ["/data"]

CMD ["python", "-m", "gateway"]