"""Diagnostic event consumers."""

from .jsonlog import EventFanout, JsonLineEventLog
from .textlog import TextEventLog

__all__ = ["EventFanout", "JsonLineEventLog", "TextEventLog"]
