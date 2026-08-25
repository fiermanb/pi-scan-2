"""Typed values shared by CHDK discovery and transport."""

from dataclasses import dataclass

from pi_scan.cameras.base import CameraIdentity

from .errors import ChdkDiscoveryParseError

_LUA_PATTERN_MAGIC = frozenset("^$()%.[]*+-?")


def escape_lua_pattern(value: str) -> str:
    """Escape a literal USB selector for chdkptp's Lua-pattern matching."""
    return "".join(
        f"%{character}" if character in _LUA_PATTERN_MAGIC else character for character in value
    )


@dataclass(frozen=True, slots=True)
class ChdkConnection:
    bus: str
    device: str

    def cli_spec(self) -> str:
        return f"-b={escape_lua_pattern(self.bus)} -d={escape_lua_pattern(self.device)}"


@dataclass(frozen=True, slots=True)
class ChdkDevice:
    index: int
    model: str
    bus: str
    device: str
    vendor_id: str
    product_id: str
    serial_number: str | None
    status: str

    @property
    def connection(self) -> ChdkConnection:
        return ChdkConnection(self.bus, self.device)

    @property
    def identity(self) -> CameraIdentity:
        """Identify the camera by serial number only, as Pi Scan 1.5 did.

        A port-derived identifier would change whenever a camera is moved to
        another USB socket, silently detaching it from its saved configuration.
        """
        if self.serial_number is None:
            raise ChdkDiscoveryParseError(
                f"CHDK camera {self.model!r} reports no serial number; "
                "Pi Scan keys its configuration by serial"
            )
        return CameraIdentity(identifier=self.serial_number, model=self.model, backend="chdk")
