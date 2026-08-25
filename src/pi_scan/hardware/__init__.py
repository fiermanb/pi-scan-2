"""Optional hardware adapters."""

from pi_scan.hardware.pedal import FakePedal, GpioZeroPedal, Pedal, PedalUnavailable

__all__ = ["FakePedal", "GpioZeroPedal", "Pedal", "PedalUnavailable"]
