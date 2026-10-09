#!/usr/bin/env python3
"""Replace the pip-vendored `requests` inside a BUNDLED WHEEL with the stub.

WHY THIS FILE EXISTS
====================
The base image ships `ensurepip`, and `ensurepip` carries a complete pip wheel:

    /usr/local/lib/python3.9/ensurepip/_bundled/pip-23.0.1-py3-none-any.whl

A wheel is a ZIP, so that archive is READABLE SOURCE with no install, no network
and no checkout. Measured: it carries the SAME oracle as the site-packages copy,
at `pip/_vendor/requests/sessions.py:259` --

    # Override the original request.
    req = prepared_request

which is the gold fix for psf__requests-1963 (base commit requests 2.3.0, wheel
carries 2.28.2). Stripping site-packages alone therefore did NOT close the
vendored-oracle finding: the same fix stayed readable one directory over.

WHY NOT JUST DELETE THE WHEEL
=============================
Measured, as root, on the built image: moving it away breaks venv creation --

    $ mv .../pip-23.0.1-py3-none-any.whl /tmp/ && python -m venv /tmp/v2
    venv FAILED without bundled wheel
    $ python -m ensurepip --default-pip
    FileNotFoundError: .../ensurepip/_bundled/pip-23.0.1-py3-none-any.whl

So deletion would take away the ability to bootstrap a venv, which the agent
legitimately needs. The wheel stays; only its vendored `requests` is replaced,
exactly as docker/sandbox.Dockerfile does for the installed copy.

WHAT IT DOES
============
1. Drops every entry under `pip/_vendor/requests/` from the wheel.
2. Adds the source-free stub (docker/pip_vendor_stub/requests/) in its place.
3. Recomputes the `RECORD` lines for the changed files, so the archive stays a
   well-formed wheel (pip uses RECORD for uninstall bookkeeping and
   `--require-hashes`; a stale RECORD would be a latent breakage).
4. Writes the result back over the same path, keeping the filename: ensurepip
   locates its wheel by name in `_bundled/`.

Usage:
    python strip_ensurepip_oracle.py WHEEL STUB_DIR
"""
from __future__ import annotations

import base64
import hashlib
import os
import pathlib
import shutil
import sys
import zipfile

VENDOR_PREFIX = "pip/_vendor/requests/"


def _record_line(name: str, blob: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(blob).digest())
    return f"{name},sha256={digest.rstrip(b'=').decode()},{len(blob)}"


def rewrite(wheel: pathlib.Path, stub: pathlib.Path) -> int:
    if not wheel.is_file():
        print(f"FATAL: bundled wheel not found: {wheel}", file=sys.stderr)
        return 1
    if not stub.is_dir():
        print(f"FATAL: stub directory not found: {stub}", file=sys.stderr)
        return 1

    with zipfile.ZipFile(wheel) as z:
        infos = z.infolist()
        payload = {i.filename: z.read(i.filename) for i in infos}

    replaced = [n for n in payload if n.startswith(VENDOR_PREFIX)]
    if not replaced:
        # Nothing to strip means the wheel layout changed and this script is now
        # a no-op -- which would silently reopen the oracle. Fail loudly instead.
        print(
            f"FATAL: no entries under {VENDOR_PREFIX!r} in {wheel.name}; the "
            f"layout changed, so this stripper no longer protects anything",
            file=sys.stderr,
        )
        return 1

    added = {}
    for path in sorted(stub.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        added[VENDOR_PREFIX + path.relative_to(stub).as_posix()] = path.read_bytes()
    if not added:
        print(f"FATAL: the stub at {stub} has no files", file=sys.stderr)
        return 1

    # --- rebuild RECORD so the archive stays consistent ----------------------
    record_name = next(
        (n for n in payload if n.endswith(".dist-info/RECORD")), None
    )
    if record_name is None:
        print(f"FATAL: no RECORD in {wheel.name}", file=sys.stderr)
        return 1

    kept_lines = []
    for line in payload[record_name].decode("utf-8").splitlines():
        name = line.split(",", 1)[0]
        if name.startswith(VENDOR_PREFIX):
            continue          # replaced below
        kept_lines.append(line)
    kept_lines += [_record_line(n, b) for n, b in added.items()]
    kept_lines.sort()
    payload[record_name] = ("\n".join(kept_lines) + "\n").encode("utf-8")

    # --- write the new archive atomically -----------------------------------
    tmp = wheel.with_suffix(wheel.suffix + ".new")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
        for info in infos:
            if info.filename.startswith(VENDOR_PREFIX):
                continue
            out.writestr(info, payload[info.filename])
        for name, blob in added.items():
            out.writestr(name, blob)
    shutil.copymode(wheel, tmp)
    os.replace(tmp, wheel)

    print(
        f"ensurepip wheel stripped: {wheel.name} -- removed {len(replaced)} "
        f"vendored requests entries, added {len(added)} stub files"
    )
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__.strip().splitlines()[-1].strip(), file=sys.stderr)
        return 2
    return rewrite(pathlib.Path(argv[1]), pathlib.Path(argv[2]))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
