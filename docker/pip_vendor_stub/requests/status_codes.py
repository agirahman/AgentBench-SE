"""Inert status-code table.

Some pip paths reference `requests.codes`; the values are HTTP constants, not
requests logic.
"""


class _Codes:
    ok = 200
    created = 201
    no_content = 204
    moved_permanently = 301
    found = 302
    not_modified = 304
    temporary_redirect = 307
    permanent_redirect = 308
    bad_request = 400
    unauthorized = 401
    forbidden = 403
    not_found = 404
    method_not_allowed = 405
    request_timeout = 408
    conflict = 409
    gone = 410
    length_required = 411
    precondition_failed = 412
    unsupported_media_type = 415
    too_many_requests = 429
    internal_server_error = 500
    bad_gateway = 502
    service_unavailable = 503
    gateway_timeout = 504


codes = _Codes()
