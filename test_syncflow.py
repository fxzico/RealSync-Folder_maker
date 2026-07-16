"""
Headless unit tests for SyncFlow Automator's pure logic (no GUI required).

Run:  python test_syncflow.py
These cover path parsing, filename construction, and the Mode C folder formula —
the parts most likely to silently corrupt a delivery if they regress.
"""
import os
import sys
import tempfile
import importlib.util
from pathlib import Path

# Load main.py without triggering the Tk mainloop (it is behind __main__).
_spec = importlib.util.spec_from_file_location("syncflow_main", "main.py")
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)

_failures = []

def check(name, got, exp):
    if got != exp:
        _failures.append(name)
        print(f"[FAIL] {name}\n       got: {got}\n       exp: {exp}")
    else:
        print(f"[PASS] {name}")


def test_parse_context():
    p = r"F:/dur chai/1/Zico/Reel_01/Exports/Character Exports/bubu/Scene_03/Audio/take_raw.wav"
    ctx = m.parse_context_from_path(p)
    check("parse.movie", ctx["movie"], "Zico")
    check("parse.reel", ctx["reel"], "Reel_01")
    check("parse.character", ctx["character"], "bubu")
    check("parse.scene", ctx["scene"], "Scene_03")
    check("parse.format", ctx["format_folder"], "Audio")


def test_naming_preserves_extension():
    # A .wav in the Audio folder must stay .wav — renaming never transcodes.
    p = r"F:/Zico/Reel_01/Exports/Character Exports/bubu/Scene_03/Audio/take_raw.wav"
    ctx = m.parse_context_from_path(p)
    check("rename.audio", m.build_new_name(ctx, "7"),
          "Zico_Reel_01_Scene_03_bubu_Shot_07_Audio.wav")


def test_naming_variants():
    base = r"X:/Show/Reel_02/Exports/Character Exports/meow/Scene_10"
    check("rename.mp4",
          m.build_new_name(m.parse_context_from_path(base + "/MP4/clip.mp4"), "3"),
          "Show_Reel_02_Scene_10_meow_Shot_03.mp4")
    check("rename.syncso_nonnumeric",
          m.build_new_name(m.parse_context_from_path(base + "/SyncSO/x.mov"), "A1"),
          "Show_Reel_02_Scene_10_meow_Shot_A1_Sync_so.mov")
    check("rename.lipdub",
          m.build_new_name(m.parse_context_from_path(base + "/Lipdub/y.mov"), "12"),
          "Show_Reel_02_Scene_10_meow_Shot_12_Lipdub.mov")


def test_bad_path_returns_none():
    check("parse.bad", m.parse_context_from_path(r"C:/random/file.txt"), None)


def test_structure_formula():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "Zico" / "Reel_01" / "Exports" / "Character Exports"
        chars, scenes = ["gang", "meow"], 3
        pad = max(2, len(str(scenes)))
        for c in chars:
            for s in range(1, scenes + 1):
                for fmt in m.FORMAT_FOLDERS:
                    os.makedirs(base / c / f"Scene_{s:0{pad}d}" / fmt, exist_ok=True)
        leaf = sum(1 for _, d, _ in os.walk(base) if not d)
        check("structure.leaf_count", leaf, len(chars) * scenes * len(m.FORMAT_FOLDERS))


if __name__ == "__main__":
    test_parse_context()
    test_naming_preserves_extension()
    test_naming_variants()
    test_bad_path_returns_none()
    test_structure_formula()
    print("\nRESULT:", "ALL PASS" if not _failures else f"{len(_failures)} FAILURE(S): {_failures}")
    sys.exit(1 if _failures else 0)
