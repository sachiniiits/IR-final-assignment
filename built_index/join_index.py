"""
Re-assemble and extract the full Hindi Wikipedia positional index.

    python join_index.py            (run inside the built_index folder)

Joins S20240010206-hiwiki-index.zip.part00 .. part06, checks SHA-256 against
SHA256SUMS.txt and extracts the folder  S20240010206-hiwiki-index/ .
Works on Windows, Linux and macOS (Python standard library only).
"""

import glob
import hashlib
import os
import zipfile

NAME = "S20240010206-hiwiki-index.zip"
HERE = os.path.dirname(os.path.abspath(__file__))


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main() -> None:
    parts = sorted(glob.glob(os.path.join(HERE, NAME + ".part*")))
    if not parts:
        raise SystemExit("no part files found next to this script")
    out = os.path.join(HERE, NAME)
    with open(out, "wb") as dst:
        for p in parts:
            with open(p, "rb") as src:
                while True:
                    block = src.read(1 << 20)
                    if not block:
                        break
                    dst.write(block)
    expected = {}
    with open(os.path.join(HERE, "SHA256SUMS.txt"), encoding="utf-8") as f:
        for line in f:
            digest, fname = line.split()
            expected[fname] = digest
    got = sha256(out)
    if got != expected[NAME]:
        raise SystemExit(f"checksum mismatch: {got} != {expected[NAME]}")
    print(f"joined {len(parts)} parts -> {NAME}  (sha256 OK)")
    with zipfile.ZipFile(out) as z:
        z.extractall(HERE)
    print(f"extracted -> {os.path.join(HERE, 'S20240010206-hiwiki-index')}")


if __name__ == "__main__":
    main()
