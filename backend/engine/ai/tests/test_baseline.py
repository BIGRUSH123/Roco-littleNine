from pathlib import Path

import pytest

from backend.engine.ai.baseline import preserve_initial_baseline


class FakeModel:
    def __init__(self, payload):
        self.payload = payload

    def save(self, path):
        Path(path).write_bytes(self.payload)


def test_m0_is_created_once_and_never_overwritten(tmp_path):
    path, created = preserve_initial_baseline(FakeModel(b"initial"), str(tmp_path))
    assert created
    assert path.name == "model_rl_m0.pt"
    second, created = preserve_initial_baseline(FakeModel(b"later"), str(tmp_path))
    assert second == path
    assert not created
    assert path.read_bytes() == b"initial"
    assert list(tmp_path.iterdir()) == [path]


def test_failed_save_does_not_publish_partial_m0(tmp_path):
    class FailingModel:
        def save(self, path):
            Path(path).write_bytes(b"partial")
            raise RuntimeError("failed")

    with pytest.raises(RuntimeError, match="failed"):
        preserve_initial_baseline(FailingModel(), str(tmp_path))
    assert list(tmp_path.iterdir()) == []
