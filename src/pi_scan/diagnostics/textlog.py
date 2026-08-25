"""Human-readable diagnostic log in the Pi Scan 1.5 error.log format."""

import json
from pathlib import Path
from threading import Lock

from pi_scan.events import ApplicationEvent

_TIMESTAMP = "%Y-%m-%d %H:%M:%S"


class TextEventLog:
    """Append events as timestamped lines, as Pi Scan 1.5's errorlog did.

    Field staff read this file directly off the scan media, so it keeps the
    original ``<timestamp> -- <text>`` shape rather than the JSON-lines form.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()

    def __call__(self, event: ApplicationEvent) -> None:
        line = f"{event.timestamp.strftime(_TIMESTAMP)} -- {event.message}"
        detail = _describe(event)
        if detail:
            line += f": {detail}"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(line + "\n")
                stream.flush()


def _describe(event: ApplicationEvent) -> str:
    error = event.details.get("error")
    if isinstance(error, str) and error:
        failures = event.details.get("camera_failures")
        if isinstance(failures, dict) and failures:
            return f"{error}; {json.dumps(failures, ensure_ascii=False, sort_keys=True)}"
        return error
    return ""
