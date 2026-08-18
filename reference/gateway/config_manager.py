"""API-based configuration management with hot reload.

Allows runtime configuration changes without server restart.
Stores config in the database and provides a notification
mechanism for hot-reloading.
"""

from __future__ import annotations

import threading
from typing import Any, Callable


class ConfigManager:
    """API-based configuration management with hot reload.

    Allows runtime configuration changes without server restart.
    Stores config in the database and provides a notification
    mechanism for hot-reloading.
    """

    def __init__(self, db: Any) -> None:
        self._db = db
        self._listeners: list[Callable[[str, Any], None]] = []
        self._cache: dict[str, Any] = {}
        self._lock = threading.Lock()

    def get(self, key: str, default: Any = None) -> Any:
        """Get a config value (from cache or database)."""
        with self._lock:
            if key in self._cache:
                return self._cache[key]

        value = self._db.get_config(key)
        if value is None:
            return default

        with self._lock:
            self._cache[key] = value
        return value

    def set(self, key: str, value: Any, updated_by: str = "system") -> None:
        """Set a config value and notify listeners."""
        self._db.set_config(key, value, updated_by)
        with self._lock:
            self._cache[key] = value
        self._notify_listeners(key, value)

    def get_all(self) -> dict[str, Any]:
        """Get all config values."""
        all_config = self._db.get_all_config()
        with self._lock:
            self._cache.update(all_config)
        return dict(self._cache)

    def register_listener(self, callback: Callable[[str, Any], None]) -> None:
        """Register a listener for config changes."""
        with self._lock:
            self._listeners.append(callback)

    def _notify_listeners(self, key: str, value: Any) -> None:
        """Notify all listeners of a config change."""
        with self._lock:
            listeners = list(self._listeners)
        for callback in listeners:
            try:
                callback(key, value)
            except Exception:
                pass  # Listener failures should not crash

    def hot_reload(self) -> dict[str, Any]:
        """Reload all config from the database."""
        all_config = self._db.get_all_config()
        with self._lock:
            old_cache = dict(self._cache)
            self._cache = dict(all_config)
            # Notify for changed keys
            for key, new_value in all_config.items():
                old_value = old_cache.get(key)
                if old_value != new_value:
                    self._notify_listeners(key, new_value)
        return dict(self._cache)