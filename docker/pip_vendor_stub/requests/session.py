"""Minimal stand-in for `requests.Session`.

pip's `PipSession` SUBCLASSES this and calls `super().__init__()`, sets
`self.headers`, mounts adapters, and uses the object as a context manager
(`with session as s:`). Measured: without `__exit__` pip fails with

    AttributeError: type object 'PipSession' has no attribute '__exit__'

so the context-manager protocol is load-bearing even for a local install.
"""
from .structures import CaseInsensitiveDict


class Session:
    def __init__(self, *args, **kwargs):
        self.headers = CaseInsensitiveDict()
        self.adapters = {}
        self.auth = None
        self.hooks = {}
        self.params = {}
        self.proxies = {}
        self.stream = False
        self.verify = True
        self.cert = None
        self.max_redirects = 30
        self.trust_env = True
        self.cookies = None
        self.redirect_cache = {}
        self._closed = False

    # pip uses the session as a context manager.
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def mount(self, prefix, adapter):
        self.adapters[prefix] = adapter

    def close(self):
        self._closed = True

    # Any call that would reach the network fails loudly rather than pretending.
    def get(self, *a, **k):
        raise RuntimeError("network is disabled in this sandbox")

    def post(self, *a, **k):
        raise RuntimeError("network is disabled in this sandbox")

    def request(self, *a, **k):
        raise RuntimeError("network is disabled in this sandbox")

    def send(self, *a, **k):
        raise RuntimeError("network is disabled in this sandbox")
