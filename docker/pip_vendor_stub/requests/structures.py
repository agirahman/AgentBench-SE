"""CaseInsensitiveDict, as pip uses it for HTTP headers.

This is a generic container utility rather than requests-specific logic, but it is
written here rather than copied so this package carries no requests source.
"""


class CaseInsensitiveDict(dict):
    def __init__(self, data=None, **kwargs):
        super().__init__()
        if data is not None:
            self.update(data)
        if kwargs:
            self.update(kwargs)

    @staticmethod
    def _k(key):
        return key.lower() if isinstance(key, str) else key

    def __setitem__(self, key, value):
        super().__setitem__(self._k(key), value)

    def __getitem__(self, key):
        return super().__getitem__(self._k(key))

    def __delitem__(self, key):
        super().__delitem__(self._k(key))

    def __contains__(self, key):
        return super().__contains__(self._k(key))

    def get(self, key, default=None):
        return super().get(self._k(key), default)

    def pop(self, key, *args):
        return super().pop(self._k(key), *args)

    def setdefault(self, key, default=None):
        return super().setdefault(self._k(key), default)

    def update(self, other=None, **kwargs):
        if other:
            items = other.items() if hasattr(other, "items") else other
            for k, v in items:
                self[k] = v
        for k, v in kwargs.items():
            self[k] = v

    def copy(self):
        return CaseInsensitiveDict(self)

    def __eq__(self, other):
        if isinstance(other, dict):
            return ({self._k(k): v for k, v in self.items()}
                    == {self._k(k): v for k, v in other.items()})
        return NotImplemented

    __hash__ = None  # mutable, like the real thing
