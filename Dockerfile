# syntax=docker/dockerfile:1.7
FROM rust:1.97-slim-bookworm@sha256:2775a09d208ff0d7c1f50490c45b62db929e87ba1dcbc3f2132ac71a704bcdd3 AS builder

WORKDIR /src
COPY Cargo.toml Cargo.lock ./
COPY crates/ ./crates/
COPY bindings/ ./bindings/
COPY xtask/ ./xtask/
RUN cargo build --locked --release -p noerelay-gateway

FROM debian:bookworm-slim@sha256:abd67ffcfa541b485a3dff59865ab629aa048a6c613e639d36e7456b0b229241 AS runtime

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system noerelay \
    && useradd --system --gid noerelay --home-dir /app --shell /usr/sbin/nologin noerelay

WORKDIR /app
COPY --from=builder --chown=noerelay:noerelay /src/target/release/noerelay-gateway /usr/local/bin/noerelay-gateway

ENV NOERELAY_BIND=0.0.0.0:8080 \
    RUST_BACKTRACE=0

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl --fail --silent --show-error http://127.0.0.1:8080/ready >/dev/null

USER noerelay
ENTRYPOINT ["/usr/local/bin/noerelay-gateway"]
