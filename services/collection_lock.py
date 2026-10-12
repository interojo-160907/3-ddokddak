"""Serialize collector processes; the OS releases the lock after a crash."""
from contextlib import contextmanager
from functools import wraps
import os
from pathlib import Path
import threading

from services.data_location import resolve_data_root

_state = threading.local()


@contextmanager
def collection_lock(root=None):
    path = Path(root or resolve_data_root()) / "backward-v1" / "collection.lock"
    key = str(path.resolve())
    if getattr(_state, "key", None) == key:
        yield
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if path.stat().st_size == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("다른 데이터 수집이 진행 중입니다. 완료 후 다시 실행해 주세요.") from exc
        previous = getattr(_state, "key", None)
        _state.key = key
        try:
            yield
        finally:
            _state.key = previous
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def serialized_collection(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with collection_lock():
            return function(*args, **kwargs)
    return wrapped
