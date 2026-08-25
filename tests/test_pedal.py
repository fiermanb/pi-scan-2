import pytest

from pi_scan.hardware.pedal import FakePedal, GpioZeroPedal, PedalUnavailable


class ButtonStub:
    def __init__(self, pin, **options):
        self.pin = pin
        self.options = options
        self.is_pressed = False
        self.when_pressed = None
        self.when_released = None
        self.closed = False

    def close(self):
        self.closed = True


def test_fake_pedal_emits_active_low_edges():
    levels = []
    pedal = FakePedal(levels.append)
    pedal.press()
    pedal.release()
    assert levels == [1, 0, 1]


def test_fake_pedal_rejects_bad_level_and_use_after_close():
    pedal = FakePedal(lambda level: None)
    with pytest.raises(ValueError):
        pedal.set_level(2)
    pedal.close()
    with pytest.raises(RuntimeError):
        pedal.press()


def test_gpiozero_adapter_configures_pullup_and_disconnects_callbacks():
    levels = []
    pedal = GpioZeroPedal(levels.append, pin=17, bounce_time=0.1, button_factory=ButtonStub)
    button = pedal._button
    assert (button.pin, button.options) == (17, {"pull_up": True, "bounce_time": 0.1})
    assert levels == [1]
    button.when_pressed()
    button.when_released()
    assert levels == [1, 0, 1]
    pedal.close()
    assert button.closed and button.when_pressed is None and button.when_released is None


def test_gpiozero_initialization_failure_is_typed():
    def unavailable_factory(pin, **options):
        raise OSError("GPIO permission denied")

    with pytest.raises(PedalUnavailable, match="BCM pin 21") as raised:
        GpioZeroPedal(lambda level: None, button_factory=unavailable_factory)
    assert isinstance(raised.value.__cause__, OSError)
