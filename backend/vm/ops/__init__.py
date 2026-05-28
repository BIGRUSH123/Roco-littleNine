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
from .inherit_effects import op_inherit_effects
from .interrupt import op_interrupt
from .lives_change import op_lives_change
from .lock import op_lock
from .mark import op_mark
from .mod import (
    op_energize,
    op_flag_set,
    op_heal,
    op_mod,
    op_mult_mod,
    op_power_mod,
    op_revive,
    op_stat_stage,
)
from .redirect import op_redirect
from .replay import op_replay
from .reset import op_reset
from .return_ import op_return
from .schedule import op_schedule
from .steal import op_steal
from .team_counter_write import op_team_counter_write
from .tick import op_tick
from .trait_interaction import op_trait_interaction
from .transform import op_transform
from .weather import op_weather
