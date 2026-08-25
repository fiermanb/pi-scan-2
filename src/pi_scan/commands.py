"""Single-operation asynchronous runner for UI integrations."""

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from enum import StrEnum
from threading import Lock
from typing import Any

from pi_scan.application import PiScanApplication


class ApplicationCommand(StrEnum):
    INITIALIZE = "initialize"
    PREPARE = "prepare"
    FOCUS = "focus"
    CAPTURE = "capture"
    RESCAN = "rescan"
    DISMISS_FAILURE = "dismiss_failure"
    RECOVER = "recover"
    SWAP_CAMERAS = "swap_cameras"
    TURN_OFF_CAMERAS = "turn_off_cameras"
    APPLY_UPDATE = "apply_update"
    FINISH = "finish"
    SAVE_DEBUG_LOGS = "save_debug_logs"


class CommandInProgress(RuntimeError):
    """A UI command was rejected because another hardware operation is active."""


class ApplicationCommandRunner:
    """Execute one application operation at a time outside the UI thread."""

    def __init__(self, application: PiScanApplication) -> None:
        self.application = application
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pi-scan-app")
        self._lock = Lock()
        self._active: Future[Any] | None = None
        self._closed = False

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._active is not None

    def submit(self, command: ApplicationCommand) -> Future[Any]:
        return self.submit_action(command.value, lambda: self._execute(command))

    def submit_action(self, name: str, action: Callable[[], Any]) -> Future[Any]:
        with self._lock:
            if self._closed:
                raise RuntimeError("command runner is closed")
            if self._active is not None:
                raise CommandInProgress(f"cannot start {name}; another operation is active")
            future = self._executor.submit(action)
            self._active = future
        future.add_done_callback(self._completed)
        return future

    def shutdown(self, *, wait: bool = True) -> None:
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=wait, cancel_futures=True)

    def _execute(self, command: ApplicationCommand) -> object:
        operation = getattr(self.application, command.value)
        return operation()

    def _completed(self, future: Future[Any]) -> None:
        with self._lock:
            if self._active is future:
                self._active = None
