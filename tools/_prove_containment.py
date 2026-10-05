"""Prove the leak-detection test CAN fail.

A test that cannot fail is decoration. test_config_values_are_restored_after_the_reload
asserts config.Config does NOT still hold the values test_config_from_env installed.
If the restore fixture were removed, that assertion should FAIL. This script disables
the fixture and confirms it does -- otherwise the test is not load-bearing.

The first version of that test asserted module class-identity instead, and it passed
with the fixture DISABLED, because whether the modules agree depends on import order.
This script is what caught that. Restores the file from an in-memory copy, always.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "tests" / "test_response_utils.py"
TEST_NAME = "test_config_values_are_restored_after_the_reload"

original = TARGET.read_text(encoding="utf-8")

# Read and write BYTES, preserving the file's line endings. Writing text here once
# converted the whole file to CRLF, which showed up as a 374-line diff of a file whose
# content had not changed -- a false signal that cost time to diagnose.
original_bytes = TARGET.read_bytes()
original_text = original_bytes.decode("utf-8")
newline = "\r\n" if b"\r\n" in original_bytes else "\n"

# Remove the RESTORING reload inside test_config_from_env's finally block -- the one
# after the env is put back. The reload inside the `try` must stay: that is the
# behaviour under test.
# Normalise to \n for the regex, then restore the file's own newline on write.
normalised = original_text.replace("\r\n", "\n")
patched, n = re.subn(
    r"(\n        for key, value in before\.items\(\):\n"
    r"            if value is None:\n"
    r"                os\.environ\.pop\(key, None\)\n"
    r"            else:\n"
    r"                os\.environ\[key\] = value\n)"
    r"        importlib\.reload\(config_module\)\n",
    r"\1",
    normalised,
)
print(f"restoring reload removed: {n} occurrence(s)")
if n != 1:
    print("could not disable exactly one restoring reload; aborting rather than guessing")
    sys.exit(2)

try:
    TARGET.write_bytes(patched.replace("\n", newline).encode("utf-8"))
    # Run BOTH tests in file order. Running the leak test alone proves nothing: it
    # only has something to detect if test_config_from_env ran first and left its env
    # values installed. That mistake is why this script exists.
    out = subprocess.run(
        [sys.executable, "-m", "pytest",
         "tests/test_response_utils.py::test_config_from_env",
         f"tests/test_response_utils.py::{TEST_NAME}",
         "-q"],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    print("\n--- both tests, with the restore DISABLED ---")
    print("\n".join(out.stdout.strip().splitlines()[-8:]))
    failed = "failed" in out.stdout
    print()
    if failed:
        print("OK -- the leak test FAILS without the restore, so it genuinely detects")
        print("     the leak. It is load-bearing.")
    else:
        print("PROBLEM -- it still passes with the restore disabled. The test is not")
        print("           checking what its docstring claims.")
    sys.exit(1 if failed else 0)
finally:
    # Byte-for-byte restore, so the check leaves no diff behind.
    TARGET.write_bytes(original_bytes)
    print("\nfile restored")
