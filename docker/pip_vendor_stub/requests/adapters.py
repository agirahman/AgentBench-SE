"""Inert stand-in for `requests.adapters`.

pip defines `PipSession` and mounts adapters derived from `HTTPAdapter`, so the
class must exist and accept the constructor arguments pip passes.
"""
DEFAULT_POOLBLOCK = False


class BaseAdapter:
    def __init__(self):
        self.max_retries = 0
        self.config = {}
        self.proxy_manager = {}

    def send(self, *a, **k):
        raise RuntimeError("network is disabled in this sandbox")

    def close(self):
        pass


class HTTPAdapter(BaseAdapter):
    def __init__(self, pool_connections=10, pool_maxsize=10, max_retries=0,
                 pool_block=DEFAULT_POOLBLOCK):
        super().__init__()
        self.pool_connections = pool_connections
        self.pool_maxsize = pool_maxsize
        self.max_retries = max_retries
        self.pool_block = pool_block
