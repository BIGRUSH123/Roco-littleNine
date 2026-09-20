"""金标准稳定性守卫：重新录制对局，必须与已提交的 fixtures 逐位一致。

任何引擎行为变化（包括优化重构）若改变了可见行为，此测试立即失败，
并给出首个分歧回合与字段。这是 Rust 移植与 Python 优化的共同安全网。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, '.')

from backend.engine.differential.compare import compare_fixtures
from backend.engine.differential.recorder import load_fixture, run_recorded

FIXTURE_DIR = Path(__file__).parent / 'differential' / 'fixtures'


def _fixture_files() -> list[Path]:
    if not FIXTURE_DIR.is_dir():
        return []
    return sorted(FIXTURE_DIR.glob('battle_*.json'))


@pytest.mark.parametrize('fixture', _fixture_files(), ids=lambda p: p.stem)
def test_battle_matches_golden(fixture: Path) -> None:
    golden = load_fixture(fixture)
    actual = run_recorded(golden['seed'])
    diffs = compare_fixtures(golden, actual)
    assert not diffs, (
        f'{fixture.name} 与重录结果不一致：\n' + '\n'.join(diffs)
    )


def test_fixtures_exist() -> None:
    """fixtures 目录必须存在且非空——这是所有引擎对拍的前提。"""
    files = _fixture_files()
    assert files, (
        '未找到金标准 fixtures，请先运行: '
        f'python -m backend.engine.differential.generate_fixtures --out {FIXTURE_DIR}'
    )
