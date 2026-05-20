"""IR VM — pure-function skill execution core.

V2: Typed match/case dispatch (OP_DISPATCH removed from ops).

Public API:
    from vm import Ctx, ADDRESS_MAP, resolve, QueryRef
    from vm import eval_one, COND_EVAL
    from vm import calc_damage
    from vm import execute, process_effects, process_one
    from vm.journal import Mutation, Journal, StatChange, ModifierInjection, ...
    from vm.ops import op_mod, ...
"""

from .cond import COND_EVAL, HAVE_EVAL, eval_one
from .ctx import ADDRESS_MAP, Ctx
from .damage import calc_damage
from .executor import execute, process_effects, process_one
from .journal import (
    AbnormalChange,
    Borrow,
    Charge,
    CounterRegister,
    Damage,
    Dispel,
    Double,
    EnergyChange,
    Escape,
    Exchange,
    Heal,
    Interrupt,
    Journal,
    Lock,
    MarkChange,
    ModifierInjection,
    Mutation,
    Redirect,
    Replay,
    Reset,
    Return,
    StatChange,
    Steal,
    Tick,
    WeatherSet,
)
from .ops import op_mod
from .resolve import QueryRef, resolve
from .sort import sort_effects
