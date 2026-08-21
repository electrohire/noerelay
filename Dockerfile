# ---- Build stage ----
FROM python:3.12-slim AS builder

WORKDIR /app

# Copy only what's needed for the reference kernel
COPY reference/ ./reference/
COPY spec/ ./spec/
COPY examples/ ./examples/

# ---- Runtime stage ----
FROM python:3.12-slim AS runtime

# Create non-root user
RUN groupadd -r noerelay && useradd -r -g noerelay -d /app -s /sbin/nologin noerelay

WORKDIR /app

ENV PYTHONPATH=/app/reference

# Copy application from builder
COPY --from=builder /app/reference/ ./reference/
COPY --from=builder /app/spec/ ./spec/
COPY --from=builder /app/examples/ ./examples/

# Copy scripts (needed for model lifecycle, etc.)
COPY scripts/ ./scripts/

# No pip install — the reference kernel is dependency-free at runtime.
# Schema validation and tests are run in CI, not in the production image.

# Expose gateway port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)"

# Start gateway
ENV NOERELAY_GATEWAY_HOST=0.0.0.0
ENV NOERELAY_OPENROUTER_MODE=stub
ENV NOERELAY_DATABASE_ENABLED=1
ENV NOERELAY_DATABASE_PATH=/data/noerelay.db
ENV NOERELAY_LOG_OUTPUT=stdout

VOLUME ["/data"]

# Switch to non-root user
USER noerelay

CMD ["python", "-m", "gateway"]