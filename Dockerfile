# syntax=docker/dockerfile:1.7
FROM rust:1.98-slim-bookworm@sha256:1469a27c125cb5a3aebfa4f4e4665d935b02fb72cc093b2c974b3d740e43f157 AS builder

WORKDIR /src
COPY Cargo.toml Cargo.lock ./
COPY crates/ ./crates/
COPY bindings/ ./bindings/
COPY xtask/ ./xtask/
RUN cargo build --locked --release -p noerelay-gateway

FROM debian:bookworm-slim@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171 AS runtime

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
