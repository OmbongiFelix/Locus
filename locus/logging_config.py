"""
Structured logging setup for the Locus service.

Configures a JSON log formatter so query volume, latency, and
match_type distribution (Section 10 of the design doc) can be shipped
to log aggregation and turned into dashboards without custom parsing.
"""

import logging
import json
from datetime import datetime, timezone


class _JSONFormatter(logging.Formatter):
    """Emit each log record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj: dict = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Structured fields added via `extra={}` on the call site
        for key in ("path", "latency_ms", "status_code", "match_type"):
            value = getattr(record, key, None)
            if value is not None:
                log_obj[key] = value
        if record.exc_info and record.exc_info[1]:
            log_obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_obj)


def configure_logging() -> None:
    """Configure the root logger with the JSON formatter.

    Called exactly once, from main.py, before the app starts serving.
    Never call logging.basicConfig() elsewhere — it will silently
    override this setup.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(_JSONFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def get_logger(name: str) -> logging.Logger:
    """Return a module-scoped logger."""
    return logging.getLogger(name)
