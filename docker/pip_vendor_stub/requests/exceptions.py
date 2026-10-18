"""Inert stand-in for `requests.exceptions`.

pip catches these classes around network calls (`except requests.ConnectionError`,
`except requests.Timeout`, ...). The calls cannot happen here, but the names must
exist or the `except` clauses fail at import time.
"""


class RequestException(Exception):
    """Base class, as in requests."""


class ConnectionError(RequestException):
    pass


class Timeout(RequestException):
    pass


class ConnectTimeout(Timeout):
    pass


class ReadTimeout(Timeout):
    pass


class SSLError(ConnectionError):
    pass


class HTTPError(RequestException):
    pass


class RetryError(RequestException):
    pass


class TooManyRedirects(RequestException):
    pass


class ChunkedEncodingError(RequestException):
    pass


class InvalidURL(RequestException):
    pass


class MissingSchema(RequestException):
    pass
