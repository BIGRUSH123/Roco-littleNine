"""VM opcodes — each op_* function is a pure (Ctx, effect) -> list[Mutation] transform.

V2: Dispatch is now handled by match/case in executor.py. OP_DISPATCH has been
removed. Individual handler functions remain importable for direct use.
"""

from .abnormal import op_abnormal
from .borrow import op_borrow
from .charge import op_charge
from .count import op_count
from .dispel import op_dispel
from .double import op_double
from .escape import op_escape
from .exchange import op_exchange
from .hit import op_hit
from .interrupt import op_interrupt
from .lock import op_lock
from .mark import op_mark
from .mod import op_mod
from .redirect import op_redirect
from .replay import op_replay
from .reset import op_reset
from .return_ import op_return
from .steal import op_steal
from .tick import op_tick
from .weather import op_weather
