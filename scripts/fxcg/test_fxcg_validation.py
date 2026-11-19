"""Host regression tests for fx-CG50 linker, ROM logging and PCPK tooling."""
import re
import tempfile
import unittest
from pathlib import Path

import build_page_pak
import check_memory_layout
import rom_touch_validation

ROOT = Path(__file__).resolve().parents[2]


class MemoryLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.linker = (ROOT / "port" / "fxcg" / "fxcg.ld").read_text(encoding="utf-8")

    def test_repository_linker_layout_passes(self):
        self.assertEqual(check_memory_layout.validate_text(self.linker), [])

    def test_unvalidated_sysram_origin_is_rejected(self):
        changed = self.linker.replace(
            "sysram (rw):  o = 0x8c4e0000, l = 0x320000",
            "sysram (rw):  o = 0x8c200000, l = 0x320000",
        )
        self.assertIn("MEMORY region 'sysram'", "\n".join(check_memory_layout.validate_text(changed)))

    def test_missing_capacity_assertion_is_rejected(self):
        changed, count = re.subn(
            r"ASSERT\s*\(\s*SIZEOF\s*\(\s*\.xbss\s*\)\s*<=\s*LENGTH\s*\(\s*sysram\s*\),",
            "/* capacity assertion removed for test */",
            self.linker,
            count=1,
        )
        self.assertEqual(count, 1)
        self.assertIn(
            "missing ASSERT that .xbss fits LENGTH(sysram)",
            check_memory_layout.validate_text(changed),
        )

    def test_game_bss_cannot_move_to_internal_ram(self):
        changed = self.linker.replace("} > sysram :NONE", "} > ram :NONE", 1)
        self.assertIn(
            ".xbss must be NOLOAD and assigned to sysram with :NONE",
            check_memory_layout.validate_text(changed),
        )


class PagePakInputTests(unittest.TestCase):
    @staticmethod
    def write_capture(
        directory: Path,
        tag: str,
        pages: list[int],
        load_pages=(),
        completed: bool = True,
        status: str | None = "0\n",
        summary_override: tuple[int, int] | None = None,
    ):
        directory.mkdir(parents=True, exist_ok=True)
        records = ["load %d 0\n" % page for page in load_pages]
        records.extend("play %d 1\n" % page for page in pages)
        touch_path = directory / ("touch-" + tag + ".txt")
        touch_path.write_text("".join(records), encoding="ascii")
        if status is not None:
            (directory / ("status-" + tag + ".txt")).write_text(status, encoding="ascii")
        area, room = (int(part.replace("0x", ""), 16) for part in tag.split("-"))
        warp = "[roomcap] warp -> 1\n" if completed else "[roomcap] warp -> 0\n"
        load_count, play_count = summary_override or (len(set(load_pages)), len(set(pages)))
        log = (
            "[roomcap] post-intro seed: TABIDACHI=1\n"
            + warp
            + "[roomcap] settled requested=0x%02x/0x%02x actual=0x%02x/0x%02x\n"
            % (area, room, area, room)
            + "[ROM touch] %d load pages, %d play pages over 2 frames -> %s\n"
            % (load_count, play_count, touch_path)
        )
        (directory / ("log-" + tag + ".txt")).write_text(log, encoding="utf-8")

    def test_captures_of_same_room_across_directories_are_unioned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, second = root / "first", root / "second"
            self.write_capture(first, "01-02", [4, 7, 9])
            self.write_capture(second, "01-02", [7, 12])
            rooms, load_pages = build_page_pak.load_rooms([str(first), str(second)])
        self.assertEqual(rooms[(1, 2)], {4, 7, 9, 12})
        self.assertEqual(load_pages, set())

    def test_load_pages_are_promoted_to_every_room_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, second = root / "first", root / "second"
            self.write_capture(first, "01-02", [4, 7], load_pages=[100])
            self.write_capture(second, "03-04", [8, 9], load_pages=[101])
            rooms, load_pages = build_page_pak.load_rooms([str(first), str(second)])
        self.assertEqual(load_pages, {100, 101})
        self.assertEqual(set.intersection(*rooms.values()), {100, 101})
        self.assertEqual(rooms[(1, 2)], {4, 7, 100, 101})
        self.assertEqual(rooms[(3, 4)], {8, 9, 100, 101})

    def test_unarmed_destination_is_safely_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "capture"
            self.write_capture(directory, "03-04", [2, 5], load_pages=[100], completed=False)
            rooms, load_pages = build_page_pak.load_rooms([str(directory)])
        self.assertEqual(rooms, {})
        self.assertEqual(load_pages, set())

    def test_missing_exit_status_is_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "capture"
            self.write_capture(directory, "03-04", [2, 5], status=None)
            with self.assertRaisesRegex(ValueError, "missing exit-status file"):
                build_page_pak.load_rooms([str(directory)])

    def test_malformed_exit_status_is_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "capture"
            self.write_capture(directory, "03-04", [2, 5], status="0 unexpected\n")
            with self.assertRaisesRegex(ValueError, "invalid exit-status file"):
                build_page_pak.load_rooms([str(directory)])

    def test_nonzero_exit_status_is_fatal_even_after_warp_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "capture"
            self.write_capture(directory, "03-04", [2, 5], status="7\n")
            with self.assertRaisesRegex(ValueError, "process exit status 7"):
                build_page_pak.load_rooms([str(directory)])

    def test_touch_summary_must_match_page_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "capture"
            self.write_capture(directory, "03-04", [2, 5], summary_override=(0, 99))
            with self.assertRaisesRegex(ValueError, "touch-log counts disagree"):
                build_page_pak.load_rooms([str(directory)])

    def test_out_of_range_page_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "capture"
            self.write_capture(directory, "03-04", [build_page_pak.ROM_SIZE // build_page_pak.PAGE])
            with self.assertRaisesRegex(ValueError, "out-of-range touch-log value"):
                build_page_pak.load_rooms([str(directory)])

    def test_compressed_and_raw_pages_decode(self):
        page = b"\x00" * build_page_pak.PAGE
        packed = build_page_pak.deflate(page)
        self.assertLess(len(packed), build_page_pak.PAGE)
        self.assertEqual(build_page_pak.decode_page(packed, len(packed), 42), page)
        self.assertEqual(build_page_pak.decode_page(page, len(page), 43), page)


class EmbeddedRomPathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "port" / "port_rom.c").read_text(encoding="utf-8")

    def test_per_access_rom_dump_is_disabled_for_embedded(self):
        marker = "void Port_LogRomAccess(u32 gba_addr, const char* caller) {"
        start = self.source.index(marker)
        self.assertIn("#ifndef TMC_EMBEDDED", self.source[start:start + 160])

    def test_bulk_rom_dump_is_disabled_for_embedded(self):
        marker = "    /* ---- Extract all known ROM regions to rom_data/ ---- */"
        at = self.source.index(marker)
        self.assertIn("#ifndef TMC_EMBEDDED", self.source[at - 40:at])
        summary = self.source.index("Port_PrintRomAccessSummary();", at)
        self.assertIn("#endif /* !TMC_EMBEDDED", self.source[summary:summary + 120])


if __name__ == "__main__":
    unittest.main()
