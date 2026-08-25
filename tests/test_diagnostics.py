import json
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from pi_scan.diagnostics import EventFanout, JsonLineEventLog
from pi_scan.events import ApplicationEvent, EventKind


class JsonLineEventLogTests(TestCase):
    def test_appends_machine_readable_events(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "debug" / "events.jsonl"
            sink = JsonLineEventLog(path)
            sink(
                ApplicationEvent(
                    EventKind.STATE_CHANGED,
                    "Ready",
                    {"state": "ready"},
                    datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
                )
            )
            sink(ApplicationEvent(EventKind.OPERATION_STARTED, "Capture"))
            records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(records), 2)
            self.assertEqual(records[0]["kind"], "state_changed")
            self.assertEqual(records[0]["timestamp"], "2026-01-02T03:04:05+00:00")
            self.assertEqual(records[0]["details"]["state"], "ready")

    def test_fanout_isolates_failing_diagnostic_consumers(self) -> None:
        delivered = []
        failures = []

        def broken(event):
            raise OSError("drive removed")

        sink = EventFanout((broken, delivered.append), on_error=failures.append)
        event = ApplicationEvent(EventKind.OPERATION_STARTED, "Capture")
        sink(event)
        self.assertEqual(delivered, [event])
        self.assertEqual(str(failures[0]), "drive removed")
