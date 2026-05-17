"""IR VM — pure-function skill execution core.

Public API:
    from vm import Ctx, ADDRESS_MAP, resolve, QueryRef
    from vm import eval_one, COND_EVAL
    from vm import sort_effects, execute, VMResult
    from vm.journal import Mutation, Journal, StatChange, ModifierInjection, ...
"""

from .ctx import Ctx, ADDRESS_MAP
from .resolve import resolve, QueryRef
from .journal import (
    StatChange, ModifierInjection, Damage, Heal, EnergyChange,
    MarkChange, AbnormalChange, WeatherSet, Dispel, Steal, Tick,
    Double, Charge, Escape, Return, Lock, Interrupt, Exchange,
    Reset, Redirect, Replay, Borrow, CounterRegister,
    Mutation, Journal,
)
