import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from app.core.config import Settings

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# Attributes every LogRecord has; anything else was passed through `extra=`.
_STANDARD_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {
    "message",
    "asctime",
}
_SENSITIVE = re.compile(r"password|secret|token|cookie|authorization|api_key|credential", re.I)
REDACTED = "[redacted]"


class JsonFormatter(logging.Formatter):
    """One JSON object per line. Extra fields with sensitive names are redacted."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None) or request_id_var.get()
        if request_id:
            entry["request_id"] = request_id
        for key, value in vars(record).items():
            if key in _STANDARD_ATTRS or key == "request_id" or key.startswith("_"):
                continue
            entry[key] = REDACTED if _SENSITIVE.search(key) else value
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(settings: Settings) -> None:
    """Send application logs to stdout as JSON. Safe to call more than once."""
    logger = logging.getLogger("app")
    logger.setLevel(logging.DEBUG if settings.app_env == "development" else logging.INFO)
    if not any(getattr(h, "_nu_hub", False) for h in logger.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        handler._nu_hub = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
