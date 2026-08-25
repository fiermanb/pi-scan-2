"""Bounded, rotated preview generation for completed scan pairs."""

import os
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError

from pi_scan.domain.capture import CapturePairResult


class PreviewError(RuntimeError):
    """A captured page could not be converted into a safe preview."""


@dataclass(frozen=True, slots=True)
class PreviewImage:
    page: int
    path: Path
    width: int
    height: int
    rotation_degrees: int


@dataclass(frozen=True, slots=True)
class PreviewPair:
    even: PreviewImage
    odd: PreviewImage


@dataclass(frozen=True, slots=True)
class DetailPair:
    even: PreviewImage
    odd: PreviewImage


class PreviewGenerator:
    def __init__(
        self,
        directory: Path,
        *,
        maximum_size: tuple[int, int] = (1600, 1200),
        maximum_source_pixels: int = 100_000_000,
    ) -> None:
        if maximum_size[0] <= 0 or maximum_size[1] <= 0:
            raise ValueError("preview dimensions must be positive")
        if maximum_source_pixels <= 0:
            raise ValueError("maximum_source_pixels must be positive")
        self.directory = directory
        self.maximum_size = maximum_size
        self.maximum_source_pixels = maximum_source_pixels

    def generate(self, capture: CapturePairResult) -> PreviewPair:
        return PreviewPair(
            even=self._generate_page(
                _jpeg(capture.even_files), capture.even_page, Image.Transpose.ROTATE_90, 90
            ),
            odd=self._generate_page(
                _jpeg(capture.odd_files), capture.odd_page, Image.Transpose.ROTATE_270, 270
            ),
        )

    def generate_detail(self, capture: CapturePairResult, centre_x: float, centre_y: float):
        """Cut an unscaled window out of each captured page.

        The fitted preview is downsampled, so magnifying it cannot show whether
        the text is sharp. Pi Scan 1.5 solved that by displaying the capture at
        native resolution in tiles; this cuts one native-resolution window
        instead, which stays inside the texture limits of the Pi's GPU.
        """
        return DetailPair(
            even=self._generate_detail_page(
                _jpeg(capture.even_files),
                capture.even_page,
                Image.Transpose.ROTATE_90,
                90,
                centre_x,
                centre_y,
                "even",
            ),
            odd=self._generate_detail_page(
                _jpeg(capture.odd_files),
                capture.odd_page,
                Image.Transpose.ROTATE_270,
                270,
                centre_x,
                centre_y,
                "odd",
            ),
        )

    def _generate_detail_page(
        self,
        source: Path,
        page: int,
        transpose: Image.Transpose,
        rotation_degrees: int,
        centre_x: float,
        centre_y: float,
        side: str,
    ) -> PreviewImage:
        if not -1.0 <= centre_x <= 1.0 or not -1.0 <= centre_y <= 1.0:
            raise ValueError("detail centre must be within -1.0 and 1.0")
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / f"detail-{side}.jpg"
        temporary = self.directory / f".{target.name}.tmp-{uuid4().hex}"
        try:
            with Image.open(source) as opened:
                if opened.width * opened.height > self.maximum_source_pixels:
                    raise PreviewError(
                        f"image exceeds preview pixel limit: {opened.width}x{opened.height}"
                    )
                image = ImageOps.exif_transpose(opened)
                image.load()
                image = image.transpose(transpose)
                image = image.crop(_detail_box(image.size, self.maximum_size, centre_x, centre_y))
                if image.mode != "RGB":
                    image = image.convert("RGB")
                image.save(temporary, format="JPEG", quality=92)
                width, height = image.size
            os.replace(temporary, target)
        except PreviewError:
            raise
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as error:
            raise PreviewError(f"failed to inspect {source}: {error}") from error
        finally:
            temporary.unlink(missing_ok=True)
        return PreviewImage(page, target, width, height, rotation_degrees)

    def generate_test(
        self,
        source: Path,
        *,
        side: str,
    ) -> PreviewImage:
        if side == "even":
            transpose, degrees, page = Image.Transpose.ROTATE_90, 90, 0
        elif side == "odd":
            transpose, degrees, page = Image.Transpose.ROTATE_270, 270, 1
        else:
            raise ValueError("test preview side must be even or odd")
        return self._generate_page(
            source,
            page,
            transpose,
            degrees,
            target_name=f"test-{side}.jpg",
        )

    def _generate_page(
        self,
        source: Path,
        page: int,
        transpose: Image.Transpose,
        rotation_degrees: int,
        target_name: str | None = None,
    ) -> PreviewImage:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / (target_name or f"{page:04d}.jpg")
        temporary = self.directory / f".{target.name}.tmp-{uuid4().hex}"
        try:
            with Image.open(source) as opened:
                if opened.width * opened.height > self.maximum_source_pixels:
                    raise PreviewError(
                        f"image exceeds preview pixel limit: {opened.width}x{opened.height}"
                    )
                image = ImageOps.exif_transpose(opened)
                image.load()
                image = image.transpose(transpose)
                image.thumbnail(self.maximum_size, Image.Resampling.LANCZOS)
                if image.mode != "RGB":
                    image = image.convert("RGB")
                image.save(temporary, format="JPEG", quality=85)
                width, height = image.size
            os.replace(temporary, target)
        except PreviewError:
            raise
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as error:
            raise PreviewError(f"failed to generate preview for {source}: {error}") from error
        finally:
            temporary.unlink(missing_ok=True)
        return PreviewImage(page, target, width, height, rotation_degrees)


def _detail_box(
    size: tuple[int, int],
    window: tuple[int, int],
    centre_x: float,
    centre_y: float,
) -> tuple[int, int, int, int]:
    """Place an unscaled window over the image, clamped inside its bounds."""
    width, height = size
    crop_width = min(window[0], width)
    crop_height = min(window[1], height)
    # Viewport offsets run -1.0 to 1.0 with the origin at the centre of the page,
    # and the vertical axis points up, as it does in the preview widget.
    centre_pixel_x = (centre_x + 1.0) / 2.0 * width
    centre_pixel_y = (1.0 - centre_y) / 2.0 * height
    left = int(round(min(max(centre_pixel_x - crop_width / 2, 0), width - crop_width)))
    upper = int(round(min(max(centre_pixel_y - crop_height / 2, 0), height - crop_height)))
    return left, upper, left + crop_width, upper + crop_height


def _jpeg(files: tuple[Path, ...]) -> Path:
    for path in files:
        if path.suffix.lower() in {".jpg", ".jpeg"}:
            return path
    raise PreviewError("capture result contains no JPEG image")
