"""Inert stand-in for `requests.auth`.

pip imports AuthBase and HTTPBasicAuth for its credential handling. The names must
exist; the implementations are never reached without a network.
"""


class AuthBase:
    def __call__(self, r):
        return r


class HTTPBasicAuth(AuthBase):
    def __init__(self, username, password):
        self.username = username
        self.password = password


class HTTPProxyAuth(HTTPBasicAuth):
    pass


class HTTPDigestAuth(AuthBase):
    def __init__(self, username, password):
        self.username = username
        self.password = password
