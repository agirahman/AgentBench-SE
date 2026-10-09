"""Minimal `requests.compat` surface.

pip and cachecontrol import through this module for `builtin_str`, `is_py3`, and
the chardet re-export.
"""
builtin_str = str
str = str          # noqa: A001 - mirrors requests' own shim
bytes = bytes      # noqa: A001
basestring = (str, bytes)
numeric_types = (int, float)
integer_types = (int,)
is_py3 = True

try:
    from pip._vendor import chardet
except Exception:  # pragma: no cover - chardet is vendored by pip
    chardet = None
