import pickle
from types import SimpleNamespace

import rpyc

SERVER_SETUP = '''
import pickle
import datetime
import MetaTrader5 as mt5

def plain(value):
    if hasattr(value, "_asdict"):
        return {key: plain(item) for key, item in value._asdict().items()}
    if isinstance(value, tuple):
        return tuple(plain(item) for item in value)
    return value
'''


def local(value):
    if isinstance(value, dict):
        return SimpleNamespace(**{key: local(item) for key, item in value.items()})
    if isinstance(value, tuple):
        return tuple(local(item) for item in value)
    return value


class RemoteMT5:

    def __init__(self, host: str, port: int, timeout_seconds: int = 300):
        self.connection = rpyc.classic.connect(host, port)
        self.connection._config["sync_request_timeout"] = timeout_seconds
        self.connection.execute(SERVER_SETUP)

    def call(self, name: str, *args, **kwargs):
        code = f"pickle.dumps(plain(mt5.{name}(*{args!r}, **{kwargs!r})))"
        return local(pickle.loads(self.connection.eval(code)))

    def __getattr__(self, name: str):
        if name.isupper():
            return self.connection.eval(f"mt5.{name}")
        return lambda *args, **kwargs: self.call(name, *args, **kwargs)
