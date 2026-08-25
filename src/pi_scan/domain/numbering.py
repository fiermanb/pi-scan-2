"""Page-number allocation compatible with the original Pi Scan filenames."""

import re
from pathlib import Path

from pi_scan.cameras.base import CAPTURE_IMAGE_SUFFIXES

_CAPTURE_PAGE = re.compile(r"^(?P<number>[0-9]+)(?P<suffix>\.[^.]+)$")


def next_even_page(image_directory: Path) -> int:
    """Return the next unused even page after existing numeric capture files."""
    largest = -1
    if image_directory.exists():
        for path in image_directory.iterdir():
            match = _CAPTURE_PAGE.fullmatch(path.name)
            if (
                match is not None
                and match.group("suffix").lower() in CAPTURE_IMAGE_SUFFIXES
                and not path.is_symlink()
                and path.is_file()
            ):
                largest = max(largest, int(match.group("number")))
    candidate = largest + 1
    return candidate if candidate % 2 == 0 else candidate + 1


def page_filename(page_number: int, suffix: str = ".jpg") -> str:
    """Format a page filename, retaining four-digit legacy padding."""
    if page_number < 0:
        raise ValueError("page number cannot be negative")
    if not suffix.startswith(".") or suffix == ".":
        raise ValueError("suffix must start with '.' and contain an extension")
    return f"{page_number:04d}{suffix}"
