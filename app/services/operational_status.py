"""Lightweight operational status registry for degraded-mode visibility."""

from collections import deque
from datetime import datetime, timedelta
from threading import Lock
from typing import Any

from app.config import get_settings

settings = get_settings()


class OperationalStatusService:
    """Store recent operational events for health/readiness introspection."""

    _events: deque[dict[str, Any]] = deque(maxlen=100)
    _lock = Lock()

    def record_event(self, component: str, level: str, message: str) -> None:
        """Record an operational event with timestamp."""
        event = {
            "component": component,
            "level": level,
            "message": message,
            "timestamp": datetime.now().isoformat(),
        }
        with self._lock:
            self._events.append(event)

    def get_recent_events(
        self, max_items: int = 10, max_age_seconds: int = 3600
    ) -> list[dict[str, Any]]:
        """Return recent events within the configured age window."""
        cutoff = datetime.now() - timedelta(seconds=max_age_seconds)
        with self._lock:
            filtered = [
                event
                for event in self._events
                if datetime.fromisoformat(event["timestamp"]) >= cutoff
            ]
        return filtered[-max_items:]

    def clear(self) -> None:
        """Clear in-memory event registry. Intended for tests."""
        with self._lock:
            self._events.clear()
