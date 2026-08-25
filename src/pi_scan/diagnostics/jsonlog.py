"""Thread-safe JSON-lines event log."""

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from threading import Lock

from pi_scan.events import ApplicationEvent


def _ignore_error(error: Exception) -> None:
    del error


class JsonLineEventLog:
    """Append application events in a machine-readable diagnostic format."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()

    def __call__(self, event: ApplicationEvent) -> None:
        record = {
            "timestamp": event.timestamp.isoformat(),
            "kind": event.kind.value,
            "message": event.message,
            "details": event.details,
        }
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(encoded + "\n")
                stream.flush()


class EventFanout:
    """Deliver each event to every sink without letting diagnostics break scanning."""

    def __init__(
        self,
        sinks: Sequence[Callable[[ApplicationEvent], None]],
        *,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        self.sinks = tuple(sinks)
        self.on_error: Callable[[Exception], None] = on_error or _ignore_error

    def __call__(self, event: ApplicationEvent) -> None:
        for sink in self.sinks:
            try:
                sink(event)
            except Exception as error:
                self.on_error(error)
