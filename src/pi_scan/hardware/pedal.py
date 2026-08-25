"""Active-low foot-pedal adapters."""

from collections.abc import Callable
from importlib import import_module
from typing import Protocol, cast

LevelHandler = Callable[[int], None]


class Pedal(Protocol):
    def close(self) -> None: ...


class ButtonLike(Protocol):
    when_pressed: Callable[[], None] | None
    when_released: Callable[[], None] | None
    is_pressed: bool

    def close(self) -> None: ...


class ButtonFactory(Protocol):
    def __call__(
        self,
        pin: int,
        *,
        pull_up: bool,
        bounce_time: float,
    ) -> ButtonLike: ...


class PedalUnavailable(RuntimeError):
    """Raised when the Raspberry Pi GPIO backend is unavailable."""


class FakePedal:
    """Controllable pedal used by the simulator and tests."""

    def __init__(self, on_level: LevelHandler, *, initial_level: int = 1) -> None:
        self._on_level = on_level
        self._level = _validate_level(initial_level)
        self._closed = False
        on_level(self._level)

    @property
    def level(self) -> int:
        return self._level

    def set_level(self, level: int) -> None:
        if self._closed:
            raise RuntimeError("pedal is closed")
        self._level = _validate_level(level)
        self._on_level(self._level)

    def press(self) -> None:
        self.set_level(0)

    def release(self) -> None:
        self.set_level(1)

    def close(self) -> None:
        self._closed = True


class GpioZeroPedal:
    """gpiozero Button wrapper using BCM numbering and an active-low input."""

    def __init__(
        self,
        on_level: LevelHandler,
        *,
        pin: int = 21,
        bounce_time: float = 0.05,
        button_factory: ButtonFactory | None = None,
    ) -> None:
        if button_factory is None:
            try:
                module = import_module("gpiozero")
                factory = cast(ButtonFactory, module.Button)
            except (ImportError, AttributeError) as error:
                raise PedalUnavailable(
                    "gpiozero is required for Raspberry Pi pedal support"
                ) from error
        else:
            factory = button_factory
        try:
            self._button: ButtonLike = factory(
                pin,
                pull_up=True,
                bounce_time=bounce_time,
            )
        except Exception as error:
            raise PedalUnavailable(f"could not initialize GPIO pedal on BCM pin {pin}") from error
        self._button.when_pressed = lambda: on_level(0)
        self._button.when_released = lambda: on_level(1)
        on_level(0 if self._button.is_pressed else 1)

    def close(self) -> None:
        self._button.when_pressed = None
        self._button.when_released = None
        self._button.close()


def _validate_level(level: int) -> int:
    if level not in {0, 1}:
        raise ValueError("pedal level must be 0 or 1")
    return level
