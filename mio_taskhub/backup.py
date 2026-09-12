# Module proxy stub — real code in ops/backup.py
import sys as _sys
import importlib as _importlib
_real_name = 'mio_taskhub.ops.backup'
class _ProxyModule:
    def __init__(self): object.__setattr__(self, '_real', None)
    def _ensure(self):
        r = object.__getattribute__(self, '_real')
        if r is None: r = _importlib.import_module(_real_name); object.__setattr__(self, '_real', r)
        return r
    def __getattr__(self, name): return getattr(self._ensure(), name)
    def __setattr__(self, name, value): setattr(self._ensure(), name, value)
    def __delattr__(self, name): delattr(self._ensure(), name)
    def __dir__(self): return dir(self._ensure())
    def __repr__(self): return f"<proxy '{_real_name}'>"
_proxy = _ProxyModule()
_sys.modules[__name__] = _proxy
