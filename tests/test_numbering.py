from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from pi_scan.domain.numbering import next_even_page, page_filename


class NumberingTests(TestCase):
    def test_empty_directory_starts_at_zero(self) -> None:
        with TemporaryDirectory() as directory:
            self.assertEqual(next_even_page(Path(directory)), 0)

    def test_continues_after_largest_page_and_rounds_to_even(self) -> None:
        with TemporaryDirectory() as directory:
            images = Path(directory)
            (images / "0000.jpg").touch()
            (images / "0005.JPG").touch()
            (images / "notes.txt").touch()
            self.assertEqual(next_even_page(images), 6)

    def test_raw_and_jpeg_variants_reserve_page_numbers_after_restart(self) -> None:
        with TemporaryDirectory() as directory:
            images = Path(directory)
            (images / "0002.jpeg").touch()
            (images / "0007.NEF").touch()
            (images / "0009.CR3").touch()
            (images / "0011.DNG").touch()
            (images / "9999.txt").touch()
            self.assertEqual(next_even_page(images), 12)

    def test_directories_and_symbolic_links_do_not_reserve_page_numbers(self) -> None:
        with TemporaryDirectory() as directory:
            images = Path(directory)
            (images / "0002.jpg").touch()
            (images / "9996.jpg").mkdir()
            symbolic = images / "9998.jpg"
            symbolic.touch()
            real_is_symlink = Path.is_symlink

            def selected_path_is_symlink(path):
                return path == symbolic or real_is_symlink(path)

            with patch.object(Path, "is_symlink", selected_path_is_symlink):
                self.assertEqual(next_even_page(images), 4)

    def test_filename_retains_legacy_padding(self) -> None:
        self.assertEqual(page_filename(7), "0007.jpg")
        self.assertEqual(page_filename(12_345, ".dng"), "12345.dng")

    def test_negative_page_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            page_filename(-1)
