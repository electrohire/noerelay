"""Run the NoeRelay OpenAI-wire gateway server.

Usage::

    python -m gateway

Requires ``reference`` on ``sys.path`` (as done by ``reference/demo.py``).
"""

from __future__ import annotations

from .config import ConfigError, GatewayConfig
from .pipeline import build_pipeline_context
from .server import _setup_graceful_shutdown, create_server


def main() -> int:
    try:
        config = GatewayConfig.from_env()
        ctx = build_pipeline_context(config)
        server = create_server(config, ctx)
        _setup_graceful_shutdown(server)
    except (ConfigError, OSError) as exc:
        print(f"gateway startup failed: {exc}")
        return 1

    host, port = server.server_address[:2]
    print(f"NoeRelay gateway listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
