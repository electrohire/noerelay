"""OpenAI-wire-compatible HTTP gateway for the EPR-1 reference kernel."""

from .config import ConfigError, GatewayConfig

__version__ = "0.1.0-draft"

__all__ = [
    "ConfigError",
    "GatewayConfig",
    "__version__",
]
