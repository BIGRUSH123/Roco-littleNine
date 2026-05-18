"""VM opcodes — each op_* function is a pure (Ctx, effect) -> list[Mutation] transform.

V2: Dispatch is now handled by match/case in executor.py. OP_DISPATCH has been
removed. Individual handler functions remain importable for direct use.
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
