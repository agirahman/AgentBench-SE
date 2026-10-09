"""Source-free stand-in for pip's vendored `requests` package.

WHY THIS PACKAGE EXISTS
=======================
pip vendors a full copy of the `requests` library. A wheel is a ZIP and so is a
site-packages tree: the vendored copy is ordinary readable Python, at

    /usr/local/lib/python3.9/site-packages/pip/_vendor/requests/sessions.py

For SWE-bench instances whose base commit is an OLD requests, that file is a
LATER release and therefore CONTAINS THE FIX. Measured on psf__requests-1963
(base commit requests 2.3.0, vendored copy 2.28.2):

    fix added lines: 2   (a comment plus one assignment)
    readable from the vendored copy : 2/2  (100%)
    readable from the checkout      : 0/2  (the fix is what the agent must write)

So the vendored copy was a complete oracle for the code fix, readable with a
plain `grep` -- no install, no network, no checkout.

WHY NOT JUST DELETE IT
======================
Measured: deleting `pip/_vendor/requests` breaks pip completely.

    $ rm -rf .../pip/_vendor/requests && pip --version
    File ".../pip/_internal/exceptions.py", line 18, in <module>
        from pip._vendor.requests.models import Request, Response
    ModuleNotFoundError: No module named 'pip._vendor.requests'

pip imports it at STARTUP, on every command including `--no-index`. Removing it
would take away the dependency-install capability the operator granted.

WHY A STUB IS ENOUGH
====================
The sandbox runs `--network=none` with `no-index = true`, so pip's HTTP path is
never exercised. Only the IMPORT SURFACE and the class hierarchy must work: pip
subclasses `requests.Session` and catches `requests.exceptions.*`. None of that
requires the real implementation, and none of it needs to carry requests source.

WHAT THIS IS NOT
================
This is not a reimplementation of requests and must never grow into one. It
exists to satisfy imports. Every method that would perform I/O raises, so a
mistaken call fails loudly instead of silently doing something surprising.
"""
from .structures import CaseInsensitiveDict as CaseInsensitiveDict
from .models import (Request as Request, Response as Response,
                     PreparedRequest as PreparedRequest)
from .exceptions import RequestException as RequestException
from .session import Session as Session

__version__ = "0.0.0+agentbench-stub"


def get(*args, **kwargs):
    raise RuntimeError("network is disabled in this sandbox")


def request(*args, **kwargs):
    raise RuntimeError("network is disabled in this sandbox")
