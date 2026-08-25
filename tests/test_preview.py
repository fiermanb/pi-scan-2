from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from PIL import Image

from pi_scan.domain.capture import CapturePairResult
from pi_scan.preview import PreviewError, PreviewGenerator


def capture_result(directory: Path, *, size: tuple[int, int] = (400, 200)):
    even = directory / "0000.jpg"
    odd = directory / "0001.jpg"
    Image.new("RGB", size, color="red").save(even)
    Image.new("RGB", size, color="blue").save(odd)
    return CapturePairResult(0, 1, (even,), (odd,))


class PreviewGeneratorTests(TestCase):
    def test_generates_side_specific_test_preview_without_numbered_name(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.jpg"
            Image.new("RGB", (40, 20), color="white").save(source)
            preview = PreviewGenerator(root / "previews").generate_test(source, side="odd")
            self.assertEqual(preview.path.name, "test-odd.jpg")
            self.assertEqual(preview.rotation_degrees, 270)
            self.assertEqual((preview.width, preview.height), (20, 40))

    def test_rejects_unknown_test_preview_side(self) -> None:
        with TemporaryDirectory() as directory, self.assertRaises(ValueError):
            PreviewGenerator(Path(directory)).generate_test(Path("unused.jpg"), side="middle")

    def test_rotates_even_and_odd_pages_and_bounds_dimensions(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            generator = PreviewGenerator(root / "previews", maximum_size=(100, 100))
            previews = generator.generate(capture_result(root))
            self.assertEqual((previews.even.width, previews.even.height), (50, 100))
            self.assertEqual((previews.odd.width, previews.odd.height), (50, 100))
            self.assertEqual(previews.even.rotation_degrees, 90)
            self.assertEqual(previews.odd.rotation_degrees, 270)
            self.assertTrue(previews.even.path.exists())
            self.assertTrue(previews.odd.path.exists())

    def test_rejects_corrupt_capture(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            even = root / "0000.jpg"
            odd = root / "0001.jpg"
            even.write_bytes(b"not-an-image")
            Image.new("RGB", (10, 10)).save(odd)
            capture = CapturePairResult(0, 1, (even,), (odd,))
            with self.assertRaises(PreviewError):
                PreviewGenerator(root / "previews").generate(capture)

    def test_rejects_source_over_configured_pixel_limit(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            capture = capture_result(root, size=(20, 20))
            with self.assertRaisesRegex(PreviewError, "pixel limit"):
                PreviewGenerator(root / "previews", maximum_source_pixels=399).generate(capture)

    def test_maps_pillow_decompression_bomb_to_preview_error(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            capture = capture_result(root)
            with (
                patch(
                    "pi_scan.preview.Image.open",
                    side_effect=Image.DecompressionBombError("unsafe dimensions"),
                ),
                self.assertRaisesRegex(PreviewError, "unsafe dimensions"),
            ):
                PreviewGenerator(root / "previews").generate(capture)

    def test_rejects_capture_without_jpeg(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "0000.dng"
            raw.write_bytes(b"raw")
            capture = CapturePairResult(0, 1, (raw,), (raw,))
            with self.assertRaisesRegex(PreviewError, "no JPEG"):
                PreviewGenerator(root / "previews").generate(capture)
