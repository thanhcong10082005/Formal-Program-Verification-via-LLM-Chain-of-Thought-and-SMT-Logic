"""
run_svcomp_translate.py - One-shot: translate every .c in sv-benchmarks-loops
into QF-LIA .py and put them under Dataset/sv-benchmarks-loops-py/.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make sibling `translate_svcomp.py` importable
sys.path.insert(0, str(Path(__file__).resolve().parent))
from translate_svcomp import translate_file  # noqa: E402


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATASET_ROOT = (PROJECT_ROOT.parent / "Dataset") if (PROJECT_ROOT.parent / "Dataset").exists() else (PROJECT_ROOT / "Dataset")
SRC_ROOT = DATASET_ROOT / "sv-benchmarks-loops" / "c"
DST_ROOT = DATASET_ROOT / "sv-benchmarks-loops-py"


def main() -> int:
    tasks = ("loop-simple", "loops-crafted-1", "loop-invariants")
    n_ok, n_fail = 0, 0
    for task in tasks:
        src_dir = SRC_ROOT / task
        dst_dir = DST_ROOT / task
        if not src_dir.exists():
            print(f"[skip] {src_dir} not found")
            continue
        for c_file in sorted(src_dir.glob("*.c")):
            py_file = dst_dir / (c_file.stem + ".py")
            try:
                translate_file(c_file, py_file)
                n_ok += 1
            except Exception as e:
                print(f"[fail] {c_file.name}: {e}")
                n_fail += 1
    print(f"Translated {n_ok} files ({n_fail} failed).")
    return 0


if __name__ == "__main__":
    sys.exit(main())