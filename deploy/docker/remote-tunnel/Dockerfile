FROM alpine:3.23@sha256:fd791d74b68913cbb027c6546007b3f0d3bc45125f797758156952bc2d6daf40
RUN apk add --no-cache autossh netcat-openbsd openssh-client \
    && addgroup -g 10001 tunnel \
    && adduser -D -H -u 10001 -G tunnel tunnel
COPY --chown=tunnel:tunnel entrypoint.sh /usr/local/bin/remote-gpu-tunnel
RUN chmod 0555 /usr/local/bin/remote-gpu-tunnel
USER tunnel
ENTRYPOINT ["/usr/local/bin/remote-gpu-tunnel"]
