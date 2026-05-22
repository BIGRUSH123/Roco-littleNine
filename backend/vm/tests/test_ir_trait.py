import pytest

from backend.vm.ir_trait import (
    AndCond,
    FnCond,
    OrCond,
    PathCond,
)
from backend.vm.ir_values import Literal


class TestTraitCondition:
    def test_path_cond(self):
        c = PathCond(path=["self", "energy"], op="gt", value=Literal(5))
        assert c.path == ["self", "energy"]
        assert c.op == "gt"

    def test_fn_cond(self):
        c = FnCond(name="is_weekend")
        assert c.name == "is_weekend"

    def test_and_cond(self):
        c = AndCond(conditions=(
            PathCond(path=["self", "hp_ratio"], op="lt", value=Literal(0.5)),
            PathCond(path=["battle", "weather"], op="eq", value=Literal("rain")),
        ))
        assert len(c.conditions) == 2

    def test_or_cond(self):
        c = OrCond(conditions=(
            PathCond(path=["self", "energy"], op="gt", value=Literal(5)),
            PathCond(path=["self", "energy"], op="lt", value=Literal(1)),
        ))
        assert len(c.conditions) == 2

    def test_cond_frozen(self):
        c = PathCond(path=["self", "energy"], op="gt", value=Literal(5))
        with pytest.raises(Exception):
            c.path = ["other"]
