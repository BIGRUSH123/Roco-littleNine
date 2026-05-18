"""VM opcodes — each op_* function is a pure (Ctx, effect) -> list[Mutation] transform.

OP_DISPATCH maps effect["op"] to handler function. The executor uses this
for O(1) dispatch.
"""

from .mod import op_mod
from .mark import op_mark
from .abnormal import op_abnormal
from .weather import op_weather
from .charge import op_charge
from .tick import op_tick
from .double import op_double
from .dispel import op_dispel
from .steal import op_steal
from .escape import op_escape
from .return_ import op_return
from .lock import op_lock
from .interrupt import op_interrupt
from .exchange import op_exchange
from .reset import op_reset
from .redirect import op_redirect
from .replay import op_replay
from .borrow import op_borrow
from .hit import op_hit
from .count import op_count

# No-op: damage is handled implicitly by the engine for attack-type skills.
# The "damage" opcode in some skill JSONs is a declarative marker.
def _op_noop(ctx, effect):
    return []

OP_DISPATCH = {
    "mod": op_mod,
    "mark": op_mark,
    "abnormal": op_abnormal,
    "weather": op_weather,
    "charge": op_charge,
    "tick": op_tick,
    "double": op_double,
    "dispel": op_dispel,
    "steal": op_steal,
    "escape": op_escape,
    "return": op_return,
    "lock": op_lock,
    "interrupt": op_interrupt,
    "exchange": op_exchange,
    "reset": op_reset,
    "redirect": op_redirect,
    "replay": op_replay,
    "borrow": op_borrow,
    "hit": op_hit,
    "count": op_count,
    "damage": _op_noop,
}
