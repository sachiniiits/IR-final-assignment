"""
Cross-platform packaging (Windows / Linux / macOS, Python standard library only).

    python make_submission.py S20240010206

creates  <RollNo>-A1-PostIndex-IR-M2026.zip  containing EXACTLY the 3 files asked for
    README.txt
    <RollNo>-src.zip      (src/, tests/, sample_data/, output/ extras, README.md, scripts)
    output.txt            (= output/test_cases.txt: 10 rare terms + 5 test cases)
"""

import io
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC_ITEMS = ["src", "tests", "sample_data", "output", "README.md", "make_submission.py", "make_submission.sh"]


def add_tree(zf: zipfile.ZipFile, path: str, arc_prefix: str = "") -> None:
    full = os.path.join(ROOT, path)
    if os.path.isfile(full):
        zf.write(full, arc_prefix + path.replace(os.sep, "/"))
        return
    for dirpath, dirs, files in os.walk(full):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for f in sorted(files):
            if f.endswith(".pyc"):
                continue
            fp = os.path.join(dirpath, f)
            rel = os.path.relpath(fp, ROOT).replace(os.sep, "/")
            zf.write(fp, arc_prefix + rel)


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: python make_submission.py <RollNo>")
    roll = sys.argv[1]
    src_buf = io.BytesIO()
    with zipfile.ZipFile(src_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in SRC_ITEMS:
            if os.path.exists(os.path.join(ROOT, item)):
                add_tree(zf, item)
    out = os.path.join(ROOT, f"{roll}-A1-PostIndex-IR-M2026.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(os.path.join(ROOT, "README.md"), "README.txt")
        zf.writestr(f"{roll}-src.zip", src_buf.getvalue())
        zf.write(os.path.join(ROOT, "output", "test_cases.txt"), "output.txt")
    print(f"created {out}")
    with zipfile.ZipFile(out) as zf:
        for info in zf.infolist():
            print(f"  {info.file_size:>10,}  {info.filename}")


if __name__ == "__main__":
    main()
