"""Persistent logs must outlive construction and close at their public boundary."""

import json

import pytest

from backend.engine.ai.battle_log import BattleLogWriter
from backend.engine.ai.run_logger import RunLogger


def test_battle_log_stays_open_until_close_and_flushes(tmp_path):
    writer = BattleLogWriter(tmp_path, buffer_size=100)
    assert not writer._fp.closed
    writer.write({"turns": 3})
    writer.close()
    assert writer._fp.closed
    assert json.loads(writer.path.read_text(encoding="utf-8")) == {"turns": 3}


def test_battle_log_context_closes_on_exception(tmp_path):
    with pytest.raises(RuntimeError, match="stop"), BattleLogWriter(tmp_path) as writer:
        writer.write({"turns": 4})
        raise RuntimeError("stop")
    assert writer._fp.closed
    assert json.loads(writer.path.read_text(encoding="utf-8")) == {"turns": 4}


def test_metrics_remain_open_until_finalize(tmp_path):
    logger = RunLogger(tmp_path, run_name="lifetime")
    assert not logger._metrics_fp.closed
    logger.record_iteration({"iteration": 1, "promoted": False})
    logger.finalize()
    assert logger._metrics_fp.closed
    records = [json.loads(line) for line in logger.metrics_path.read_text(encoding="utf-8").splitlines()]
    assert records[0]["type"] == "run_start"
    assert records[-1]["type"] == "run_end"
