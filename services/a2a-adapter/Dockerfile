# syntax=docker/dockerfile:1.7
FROM golang:1.26.6-alpine@sha256:3889b425f035be855a72fb4755265311293b6d414521f0a519d819df32222d83 AS builder

WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/noerelay-a2a .

FROM alpine:3.23@sha256:fd791d74b68913cbb027c6546007b3f0d3bc45125f797758156952bc2d6daf40
RUN apk add --no-cache ca-certificates \
    && addgroup -S noerelay \
    && adduser -S -G noerelay -h /app noerelay
COPY --from=builder --chown=noerelay:noerelay /out/noerelay-a2a /usr/local/bin/noerelay-a2a
USER noerelay
EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:8090/health || exit 1
ENTRYPOINT ["/usr/local/bin/noerelay-a2a"]
