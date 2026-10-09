"""Alias `requests.packages.urllib3` onto pip's own vendored urllib3.

cachecontrol (vendored by pip) imports

    from pip._vendor.requests.packages.urllib3.response import HTTPResponse

In real requests, `requests.packages.urllib3` re-exports its bundled urllib3. pip
ships urllib3 separately under `pip._vendor.urllib3`, so this points at that copy
instead of duplicating it -- which also keeps this package free of requests source.
"""
import sys

from pip._vendor import urllib3

_prefix = "pip._vendor.requests.packages."
sys.modules[_prefix + "urllib3"] = urllib3

for _sub in ("response", "util", "exceptions", "connectionpool", "poolmanager",
             "filepost", "fields", "request", "contrib"):
    try:
        _m = __import__(f"pip._vendor.urllib3.{_sub}", fromlist=["_"])
        sys.modules[_prefix + "urllib3." + _sub] = _m
    except Exception:
        pass

packages = urllib3
