# tests/test_project_collision.py
from __future__ import annotations

from scrollstrip.project import unique_chapter_dir


# Review Focus #2
def test_second_chapter_with_the_same_name_gets_its_own_directory(tmp_path):
    first = unique_chapter_dir(tmp_path, "Swamp Thing 01")
    first.mkdir()
    (first / "project.json").write_text("{}", encoding="utf-8")
    second = unique_chapter_dir(tmp_path, "Swamp Thing 01")
    assert second != first
    assert not second.exists()


def test_name_is_slugified_for_the_filesystem(tmp_path):
    got = unique_chapter_dir(tmp_path, "Swamp Thing: Quest / Part 1?")
    assert got.parent == tmp_path
    for ch in ':/?*"<>|':
        assert ch not in got.name


def test_blank_name_still_produces_a_directory(tmp_path):
    got = unique_chapter_dir(tmp_path, "   ")
    assert got.name


def test_windows_reserved_device_names_are_rewritten(tmp_path):
    assert unique_chapter_dir(tmp_path, "CON").name == "CON-chapter"
    assert unique_chapter_dir(tmp_path, "nul").name == "nul-chapter"
    assert unique_chapter_dir(tmp_path, "COM1").name == "COM1-chapter"
    assert unique_chapter_dir(tmp_path, "NUL.txt").name == "NUL.txt-chapter"
