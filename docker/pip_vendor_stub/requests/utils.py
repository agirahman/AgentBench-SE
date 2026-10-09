"""Inert stand-in for `requests.utils`.

pip imports `get_netrc_auth`, which reads ~/.netrc for credentials. Returning None
is the correct behaviour when there is no network and no credentials file.
"""


def get_netrc_auth(url, raise_errors=False):
    return None


def default_headers():
    return {}


def default_user_agent(name="python-requests"):
    return name
