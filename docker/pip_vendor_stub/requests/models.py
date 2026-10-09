"""Inert stand-in for `requests.models`.

pip imports Request/Response/PreparedRequest/CONTENT_CHUNK_SIZE from here, and
uses Response for type annotations and `iter_content` on the download path. That
path cannot run (the sandbox has no network), so these types only need to exist
and be constructible.
"""
CONTENT_CHUNK_SIZE = 10 * 1024


class Request:
    def __init__(self, method=None, url=None, headers=None, files=None, data=None,
                 params=None, auth=None, cookies=None, hooks=None, json=None):
        self.method = method
        self.url = url
        self.headers = headers or {}
        self.files = files
        self.data = data
        self.params = params
        self.auth = auth
        self.cookies = cookies
        self.hooks = hooks
        self.json = json


class Response:
    def __init__(self):
        self.status_code = None
        self.headers = {}
        self.url = None
        self.encoding = None
        self.raw = None
        self.reason = None
        self.request = None
        self.content = b""
        self._content_consumed = False

    @property
    def ok(self):
        return False

    def iter_content(self, chunk_size=1, decode_unicode=False):
        return iter(())

    def raise_for_status(self):
        raise RuntimeError("network is disabled in this sandbox")

    def close(self):
        pass


class PreparedRequest:
    def __init__(self):
        self.method = None
        self.url = None
        self.headers = {}
        self.body = None

    def prepare(self, *args, **kwargs):
        return self
