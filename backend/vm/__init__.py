"""IR VM — pure-function skill execution core.

Public API:
    from vm import Ctx, ADDRESS_MAP, resolve, QueryRef
    from vm import eval_one, COND_EVAL
    from vm import calc_damage
    from vm import process_effects, process_one
    from vm.journal import Mutation, Journal, StatChange, ModifierInjection, ...
    from vm.ops import OP_DISPATCH, op_mod, ...
"""

from .ctx import Ctx, ADDRESS_MAP
from .resolve import resolve, QueryRef
from .cond import eval_one, COND_EVAL, HAVE_EVAL
from .damage import calc_damage
from .journal import (
    StatChange, ModifierInjection, Damage, Heal, EnergyChange,
    MarkChange, AbnormalChange, WeatherSet, Dispel, Steal, Tick,
    Double, Charge, Escape, Return, Lock, Interrupt, Exchange,
    Reset, Redirect, Replay, Borrow, CounterRegister,
    Mutation, Journal,
)
from .ops import OP_DISPATCH, op_mod
from .executor import execute, process_effects, process_one
from .sort import sort_effects
