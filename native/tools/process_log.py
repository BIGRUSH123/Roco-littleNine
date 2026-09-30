"""Process-scoped contexts for diagnostic logs and their stdout tee wrappers."""
from __future__ import annotations

import atexit
import sys
from contextlib import contextmanager


@contextmanager
def _log_context(path, mode, encoding):
    with open(path, mode, encoding=encoding) as stream:
        yield stream


def _contains_stream(wrapper, stream):
    # Diagnostic _Tee wrappers store their children in _s, and can be nested.
    return wrapper is stream or any(
        _contains_stream(child, stream) for child in getattr(wrapper, "_s", ())
    )


def open_process_log(path, mode="w", *, encoding="utf-8"):
    """Acquire now; flush/detach an owning tee before closing at process exit.

    Python flushes sys.stdout again after atexit. Restoring the pre-tee stream
    first prevents that final flush from touching the now-closed log file.
    """
    context = _log_context(path, mode, encoding)
    stream = context.__enter__()
    previous_stdout, previous_stderr = sys.stdout, sys.stderr

    def close():
        try:
            if _contains_stream(sys.stdout, stream):
                sys.stdout.flush()
                sys.stdout = previous_stdout
            if _contains_stream(sys.stderr, stream):
                sys.stderr.flush()
                sys.stderr = previous_stderr
        finally:
            context.__exit__(None, None, None)

    atexit.register(close)
    return stream
