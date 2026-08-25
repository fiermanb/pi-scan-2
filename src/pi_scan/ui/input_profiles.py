"""Kivy input configuration, reproducing the two Pi Scan 1.5 appliance images.

Pi Scan 1.5 shipped one application in two disk images that differed only in the
Kivy configuration copied to the scanner account's ``~/.kivy/config.ini``: one
for a mouse on any HDMI screen, one for the official Raspberry Pi touchscreen.
Its own README described the mouse variant as incompatible with touch, so the
choice was made when the card was written and never at runtime.

The same choice is kept here, made at deployment time, but applied to Kivy's
configuration object before the window exists rather than written into a user's
home directory. Kivy defaults `%(name)s` to a bare `probesysfs` unless it finds
`/opt/vc/include/bcm_host.h`, a path that no longer exists on 64-bit Raspberry
Pi OS, so both profiles name `provider=hidinput` explicitly as 1.5 did.
"""

from dataclasses import dataclass, field
from typing import Protocol


class ConfigurationTarget(Protocol):
    """The part of Kivy's Config object these profiles use."""

    def set(self, section: str, option: str, value: object) -> None: ...

    def remove_option(self, section: str, option: str) -> None: ...


@dataclass(frozen=True, slots=True)
class InputProfile:
    name: str
    description: str
    providers: dict[str, str]
    keyboard_mode: str
    touchring: bool
    removed_modules: tuple[str, ...] = field(default=("touchring",))


MOUSE = InputProfile(
    name="mouse",
    description="mouse or trackball on any HDMI screen",
    providers={
        "mouse": "mouse",
        "%(name)s": "probesysfs,provider=hidinput",
    },
    keyboard_mode="",
    touchring=True,
)

TOUCH = InputProfile(
    name="touch",
    description="official Raspberry Pi touchscreen",
    providers={
        "mouse": "mouse",
        "%(name)s": "probesysfs,provider=hidinput",
        "mtdev_%(name)s": "probesysfs,provider=mtdev",
        "hid_%(name)s": "probesysfs,provider=hidinput",
    },
    keyboard_mode="system",
    touchring=False,
)

PROFILES: dict[str, InputProfile] = {profile.name: profile for profile in (MOUSE, TOUCH)}
DEFAULT_PROFILE = MOUSE.name


def select_profile(name: str | None) -> InputProfile:
    if name is None:
        return PROFILES[DEFAULT_PROFILE]
    try:
        return PROFILES[name]
    except KeyError:
        known = ", ".join(sorted(PROFILES))
        raise ValueError(f"unknown input profile {name!r}; expected one of {known}") from None


def apply_input_profile(profile: InputProfile, config: ConfigurationTarget) -> None:
    """Configure Kivy for one profile. Must run before the window is created."""
    for option, value in profile.providers.items():
        config.set("input", option, value)
    config.set("kivy", "keyboard_mode", profile.keyboard_mode)
    if profile.touchring:
        config.set("modules", "touchring", "show_cursor=true")
    else:
        for module in profile.removed_modules:
            config.remove_option("modules", module)
