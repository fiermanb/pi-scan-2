from threading import Event
from unittest import TestCase

from pi_scan.commands import (
    ApplicationCommand,
    ApplicationCommandRunner,
    CommandInProgress,
)


class BlockingApplication:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()
        self.calls = []

    def initialize(self):
        self.calls.append("initialize")
        self.started.set()
        self.release.wait(timeout=5)
        return "initialized"

    def prepare(self):
        self.calls.append("prepare")
        return "prepared"


class ApplicationCommandRunnerTests(TestCase):
    def test_parameterized_action_uses_the_same_single_operation_gate(self) -> None:
        application = BlockingApplication()
        runner = ApplicationCommandRunner(application)
        try:
            future = runner.submit_action("custom", lambda: "configured")
            self.assertEqual(future.result(timeout=2), "configured")
        finally:
            runner.shutdown()

    def test_runs_operation_outside_caller_and_returns_result(self) -> None:
        application = BlockingApplication()
        runner = ApplicationCommandRunner(application)
        try:
            future = runner.submit(ApplicationCommand.INITIALIZE)
            self.assertTrue(application.started.wait(timeout=1))
            self.assertFalse(future.done())
            application.release.set()
            self.assertEqual(future.result(timeout=1), "initialized")
        finally:
            application.release.set()
            runner.shutdown()

    def test_rejects_duplicate_operation_while_hardware_is_busy(self) -> None:
        application = BlockingApplication()
        runner = ApplicationCommandRunner(application)
        try:
            first = runner.submit(ApplicationCommand.INITIALIZE)
            self.assertTrue(application.started.wait(timeout=1))
            with self.assertRaises(CommandInProgress):
                runner.submit(ApplicationCommand.PREPARE)
            application.release.set()
            first.result(timeout=1)
            self.assertEqual(
                runner.submit(ApplicationCommand.PREPARE).result(timeout=1),
                "prepared",
            )
        finally:
            application.release.set()
            runner.shutdown()

    def test_rejects_commands_after_shutdown(self) -> None:
        runner = ApplicationCommandRunner(BlockingApplication())
        runner.shutdown()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            runner.submit(ApplicationCommand.INITIALIZE)
