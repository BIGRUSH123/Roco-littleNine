# 格斗小九 (Roco) — Battle VM as a Platform

回合制精灵对战平台。确定性 VM 引擎 + Python AI SDK + 遗传算法锦标赛。

## Quickstart

```bash
# 1. Clone & install
git clone https://github.com/BIGRUSH123/Roco-littleNine.git
cd Roco-littleNine
pip install -e .

# 2. Write your first agent (or copy the template)
cp examples/my_agent.py my_bot.py
# Edit my_bot.py — implement select_action()

# 3. Run a tournament against built-in RandomAgent
python -m roco.tournament my_bot.py roco.ai.agent --rounds 10

# 4. Watch the ASCII matrix
#         Random  DamageAgent
#        --------------------
# Random |   ·        L
# DamageAgent |   W        ·
#
# Rank  Agent           W  L  Pct
#    1  DamageAgent    10  0 1.000
#    2  Random          0 10 0.000
```

## Writing an Agent

```python
# my_bot.py
from roco.ai.observation import Action, ActionKind, BattleObservation

class MyAgent:
    name = "MyAgent"

    def select_action(self, state: BattleObservation, legal_actions: list[Action]) -> Action:
        # state.my_sprite — your active sprite (HP, EP, skills, status)
        # state.opp_sprite — opponent's active sprite
        # legal_actions — [ActionKind.SKILL, .SWITCH, .GATHER, .ITEM, .PASS]

        # Pick the first available skill
        for a in legal_actions:
            if a.kind == ActionKind.SKILL:
                return a
        # Fall back to gather
        for a in legal_actions:
            if a.kind == ActionKind.GATHER:
                return a
        return legal_actions[0]

agent = MyAgent()  # module-level instance for auto-discovery
```

## Demo

```bash
python scripts/demo.py
```

Produces a live-updating tournament between 3 AI agents (AggroBot, HealBot, Random):

```
════════════════════════════════════════════════════════════
  格斗小九 (Roco) — Battle VM Tournament Demo
════════════════════════════════════════════════════════════

  Agents: HealBot, AggroBot, Random
  Rounds per matchup: 10
  Total matches: 60

  [####################] 60/60 (100%)  Random vs AggroBot → AggroBot  (8t 24ms)

Rank  Agent                      W  L  Pct
------------------------------------------------
   1  AggroBot                   40   0 1.000
   2  Random                     20  20 0.500
   3  HealBot                     0  40 0.000

           HealBot   AggroBot   Random
          ------------------------------
 HealBot |    ·         L         L
AggroBot |    W         ·         W
  Random |    W         L         ·

  Completed in 1.6s (60 matches, 26.7ms/match)
```

To record as GIF: `asciinema rec demo.cast -c 'python scripts/demo.py' && asciicast2gif demo.cast docs/demo.gif`

## Architecture

```
roco/ai/          Python AI SDK (BattleAgent protocol, observation types)
roco/bridge.py    VM-to-SDK bridge (build_observation, adapt_agent)
roco/tournament.py  Round-robin tournament runner + CLI
backend/vm/       Battle VM — compiler pipeline (Parse → Expand → Validate → Execute)
backend/engine/   VM executor + Ctx snapshot
backend/sim/      Prototype sim layer (sprites, skills, battle engine)
```

## Development

```bash
pip install -e ".[dev]"
pytest                  # 350+ tests
ruff check .            # lint
```

## License

MIT
