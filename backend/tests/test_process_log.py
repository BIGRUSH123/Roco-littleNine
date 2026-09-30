"""Atexit must detach stdout tees before closing their process-scoped logs."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("nested", [False, True])
def test_process_log_flushes_utf8_without_closed_stdout_error(tmp_path, nested):
    root = Path(__file__).resolve().parents[2]
    script = '''
import sys
from native.tools.process_log import open_process_log
class Tee:
    def __init__(self, *streams):
        self._s = streams
    def write(self, text):
        for stream in self._s:
            stream.write(text)
    def flush(self):
        for stream in self._s:
            stream.flush()
log = open_process_log(sys.argv[1])
sys.stdout = Tee(sys.stdout, log)
if sys.argv[3] == 'True':
    other = open_process_log(sys.argv[2])
    sys.stdout = Tee(sys.stdout, other)
print('完整日志：你好')
'''
    first, second = tmp_path / "first.log", tmp_path / "second.log"
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", script, str(first), str(second), str(nested)],
        cwd=root, capture_output=True, text=True, encoding="utf-8", check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout == "完整日志：你好\n"
    assert first.read_text(encoding="utf-8") == result.stdout
    if nested:
        assert second.read_text(encoding="utf-8") == result.stdout
