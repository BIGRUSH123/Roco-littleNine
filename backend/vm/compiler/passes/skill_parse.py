"""Pass 1: SkillParsePass — parse skill JSON effects into SkillIROp list."""
from __future__ import annotations

from backend.vm.compiler.context import CompileError, CompilerContext
from backend.vm.ctx import ADDRESS_MAP
from backend.vm.ir_skill import (
    AbnormalOp,
    AndCond,
    BorrowOp,
    ChargeOp,
    CondExpr,
    CountOp,
    DispelOp,
    DoubleOp,
    EnergizeOp,
    EscapeOp,
    ExchangeOp,
    FlagSetOp,
    GainSkills,
    HealOp,
    HitOp,
    InheritEffects,
    InterruptOp,
    LivesChange,
    LockOp,
    MarkOp,
    ModOp,
    MultModOp,
    NotCond,
    OrCond,
    PowerModOp,
    RedirectOp,
    ReplayOp,
    ResetOp,
    ReturnOp,
    ReviveOp,
    Schedule,
    SkillCondition,
    SkillIROp,
    StatStageOp,
    StealOp,
    TeamCounterWrite,
    TickOp,
    TraitInteraction,
    Transform,
    WeatherOp,
    WhenBlock,
    WhenBranch,
)
from backend.vm.ir_values import IRValue, Literal, Query


class SkillParsePass:
    """Parse skill JSON effects array into SkillIROp intermediate representation."""

    def process(self, ctx: CompilerContext) -> None:
        effects = ctx.raw.get("effects", [])
        for i, effect in enumerate(effects):
            try:
                op = self._parse_effect(effect)
                ctx.ir.append(op)
            except Exception as e:
                ctx.errors.append(CompileError(
                    op_index=i,
                    message=f"Parse error: {e}",
                    field=effect.get("op", effect.get("when", {}).get("cond", ""))
                ))

    # ── effect dispatcher ──

    def _parse_effect(self, effect: dict) -> SkillIROp:
        if "when" in effect and "op" not in effect:
            return self._parse_when_block(effect)
        if "op" in effect:
            op_type = effect["op"]
            method_name = f"_parse_{op_type}"
            method = getattr(self, method_name, None)
            if method is None:
                raise ValueError(f"Unknown op type: {op_type}")
            return method(effect)
        # Graceful fallback for common JSON data bugs
        if "effects" in effect:
            # Nested effects container without op/when — parse inner effects
            # (seen in malformed skill JSONs like 岩脉崩毁)
            raise ValueError(
                "Effect contains nested 'effects' without 'op'/'when' — "
                "likely a copy-paste error in skill JSON"
            )
        if "skill_type" in effect and "power" in effect:
            # Looks like a HitOp missing the "op" key (seen in 指指点点)
            effect_with_op = {**effect, "op": "hit", "type": effect.get("skill_type", "物攻")}
            return self._parse_hit(effect_with_op)
        raise ValueError(f"Effect must have 'op' or 'when' key: {effect}")

    # ── when block parser ──

    def _parse_when_block(self, effect: dict) -> WhenBlock:
        cond = self._parse_condition(effect["when"])
        then = tuple(self._parse_effect(e) for e in effect.get("then", []))
        else_ = tuple(self._parse_effect(e) for e in effect.get("else", []))
        # Support both "elif" (legacy) and "else_if" (RISC branch)
        elif_list = effect.get("elif", []) or effect.get("else_if", [])
        elif_ = tuple(
            WhenBranch(
                cond=self._parse_condition(branch.get("cond", branch.get("when", {}))),
                then=tuple(self._parse_effect(e) for e in branch.get("then", [])),
            )
            for branch in elif_list
        )
        return WhenBlock(
            cond=cond,
            then=then,
            else_=else_,
            elif_=elif_,
            feeds=self._str_or(effect, "feeds", ""),
            needs=self._str_or(effect, "needs", ""),
            priority=self._int_or(effect, "priority", 0),
        )

    # ── condition parser ──

    def _parse_condition(self, cond_dict: dict) -> SkillCondition:
        cond = cond_dict.get("cond", "")
        if cond in ("and", "or"):
            conditions = tuple(
                self._parse_condition(c) for c in cond_dict.get("conditions", [])
            )
            if cond == "and":
                return AndCond(conditions=conditions)
            return OrCond(conditions=conditions)
        if cond == "not":
            return NotCond(condition=self._parse_condition(cond_dict["condition"]))
        # Simple condition: extra keys become params
        params = {k: v for k, v in cond_dict.items() if k != "cond"}
        return CondExpr(cond=cond, params=params)

    # ── value / query resolution ──

    def _parse_value(self, raw) -> IRValue:
        """Convert a JSON value to Literal or Query.

        Dicts with a 'q' key are resolved to Query via ADDRESS_MAP.
        Everything else becomes a Literal.
        """
        if isinstance(raw, dict) and "q" in raw:
            return self._parse_query(raw)
        if isinstance(raw, bool):
            return Literal(value=raw)
        if isinstance(raw, (int, float, str)):
            return Literal(value=raw)
        return Literal(value=raw)

    def _parse_query(self, value: dict) -> Query:
        """Resolve a query dict to a Query IR node.

        Looks up ADDRESS_MAP[(of, q)] for O(1) field resolution at runtime.
        Derived queries (hp_missing_ratio, mark_count_both) are translated
        to equivalent queries on base fields.
        """
        q = value.get("q", "")
        of = value.get("of", "sprite_self")

        # ── Derived queries ──
        if q == "hp_missing_ratio":
            # 1.0 - hp_ratio → redirect to hp_ratio with scale=-1, offset=1
            base_of = of
            map_key = (base_of, "hp_ratio")
            if map_key not in ADDRESS_MAP:
                raise KeyError(f"Unknown query address (of={of}, q=hp_ratio)")
            field = ADDRESS_MAP[map_key]
            user_scale = value.get("scale", 1.0)
            user_offset = value.get("offset", 0)
            # result = (raw * user_scale + user_offset)
            # But we want: (1.0 - raw) * user_scale + user_offset
            # = raw * (-user_scale) + (user_scale + user_offset)
            return Query(
                field=field,
                scale=-user_scale,
                offset=user_scale + user_offset,
                per=value.get("per"),
                default=value.get("default"),
            )
        if q == "mark_count_both":
            # mark_count_own + mark_count_opp — handled by runtime resolve
            # Use mark_count_own as primary, resolve.py adds mark_count_opp
            own_key = ("team_own", "mark_count")
            if own_key not in ADDRESS_MAP:
                raise KeyError(f"Unknown query address (of=team_own, q=mark_count)")
            field = ADDRESS_MAP[own_key]
            return Query(
                field=field,
                name=value.get("name"),
                scale=value.get("scale", 1.0),
                offset=value.get("offset", 0),
                per=value.get("per"),
                default=value.get("default"),
                sub_key_field="mark_count_both",
            )

        map_key = (of, q)
        if map_key not in ADDRESS_MAP:
            raise KeyError(
                f"Unknown query address (of={of}, q={q}) — not in ADDRESS_MAP"
            )
        field = ADDRESS_MAP[map_key]
        return Query(
            field=field,
            name=value.get("name"),
            scale=value.get("scale", 1.0),
            offset=value.get("offset", 0),
            per=value.get("per"),
            default=value.get("default"),
        )

    def _parse_value_optional(self, effect: dict, key: str = "value") -> IRValue:
        """Parse a value field, returning Literal(0) if absent."""
        if key not in effect:
            return Literal(value=0)
        return self._parse_value(effect[key])

    # ── shared helpers ──

    def _str_or(self, d: dict, key: str, default: str) -> str:
        v = d.get(key)
        if v is None:
            return default
        return str(v)

    def _int_or(self, d: dict, key: str, default: int) -> int:
        v = d.get(key)
        if v is None:
            return default
        if isinstance(v, dict) and "q" in v:
            return 0
        return int(v)

    def _float_or(self, d: dict, key: str, default: float) -> float:
        v = d.get(key)
        if v is None:
            return default
        if isinstance(v, dict) and "q" in v:
            return 0.0
        return float(v)

    def _bool_or(self, d: dict, key: str, default: bool) -> bool:
        v = d.get(key)
        if v is None:
            return default
        return bool(v)

    def _parse_then(self, effect: dict) -> tuple[SkillIROp, ...]:
        then_list = effect.get("then", [])
        if not then_list:
            return ()
        return tuple(self._parse_effect(e) for e in then_list)

    def _common_fields(self, effect: dict) -> dict:
        """Extract feeds/needs/priority common to all ops."""
        return {
            "feeds": effect.get("feeds", ""),
            "needs": effect.get("needs", ""),
            "priority": effect.get("priority", 0),
        }

    # ── per-op parsers (21) ──

    def _parse_mod(self, e: dict) -> ModOp:
        # Handle 'value' field — can be absent for steps-only effects
        value = self._parse_value_optional(e, "value")
        # Parse 'steps' — can be int or query dict
        raw_steps = e.get("steps", 0)
        if isinstance(raw_steps, dict) and "q" in raw_steps:
            # Query-based steps — store in value and set steps=0
            # The engine evaluates the query at runtime
            value = self._parse_value(raw_steps)
            steps = 0
        else:
            steps = int(raw_steps) if not isinstance(raw_steps, dict) else 0
        return ModOp(
            target=self._str_or(e, "target", "sprite_self"),
            stat=self._str_or(e, "stat", ""),
            value=value,
            mode=self._str_or(e, "mode", "set"),
            scope=self._str_or(e, "scope", "battlefield"),
            steps=steps,
            on_next=self._bool_or(e, "on_next", False),
            per_hit=self._bool_or(e, "per_hit", False),
            skill_filter=e.get("skill_filter"),
            skill_where=e.get("skill_where"),
            if_type=e.get("if_type"),
            element=e.get("element"),
            per_element=self._int_or(e, "per_element", 0),
            name=e.get("name"),
            delay=self._int_or(e, "delay", 0),
            ttl=self._int_or(e, "ttl", 0),
            cooldown=self._int_or(e, "cooldown", 0),
            **self._common_fields(e),
        )

    def _parse_hit(self, e: dict) -> HitOp:
        power = self._parse_value_optional(e, "power")
        return HitOp(
            power=power,
            type=self._str_or(e, "type", "物攻"),
            element=e.get("element"),
            combo=self._int_or(e, "combo", 1),
            **self._common_fields(e),
        )

    def _parse_mark(self, e: dict) -> MarkOp:
        value = None
        if "value" in e:
            value = self._parse_value(e["value"])
        return MarkOp(
            target=self._str_or(e, "target", "sprite_self"),
            name=self._str_or(e, "name", ""),
            stacks=self._int_or(e, "stacks", 1),
            value=value,
            per_hit=self._bool_or(e, "per_hit", False),
            then=self._parse_then(e),
            **self._common_fields(e),
        )

    def _parse_abnormal(self, e: dict) -> AbnormalOp:
        return AbnormalOp(
            target=self._str_or(e, "target", "sprite_self"),
            name=self._str_or(e, "name", ""),
            stacks=self._int_or(e, "stacks", 1),
            scope=self._str_or(e, "scope", "battlefield"),
            per_hit=self._bool_or(e, "per_hit", False),
            heal_pct=self._float_or(e, "heal_pct", 0.0),
            energy_gain=self._int_or(e, "energy_gain", 0),
            then=self._parse_then(e),
            **self._common_fields(e),
        )

    def _parse_weather(self, e: dict) -> WeatherOp:
        return WeatherOp(
            weather=self._str_or(e, "weather", ""),
            turns=self._int_or(e, "turns", 8),
            **self._common_fields(e),
        )

    def _parse_dispel(self, e: dict) -> DispelOp:
        return DispelOp(
            target=self._str_or(e, "target", "sprite_self"),
            what=self._str_or(e, "what", ""),
            name=e.get("name"),
            limit=e.get("limit"),
            type_limit=e.get("type_limit"),
            **self._common_fields(e),
        )

    def _parse_steal(self, e: dict) -> StealOp:
        return StealOp(
            target=self._str_or(e, "target", "sprite_self"),
            what=self._str_or(e, "what", ""),
            name=e.get("name"),
            amount=self._int_or(e, "amount", 0),
            **self._common_fields(e),
        )

    def _parse_tick(self, e: dict) -> TickOp:
        return TickOp(
            target=self._str_or(e, "target", "sprite_self"),
            name=self._str_or(e, "name", ""),
            **self._common_fields(e),
        )

    def _parse_double(self, e: dict) -> DoubleOp:
        return DoubleOp(
            target=self._str_or(e, "target", "sprite_self"),
            what=self._str_or(e, "what", ""),
            name=e.get("name"),
            **self._common_fields(e),
        )

    def _parse_charge(self, e: dict) -> ChargeOp:
        return ChargeOp(
            target=self._str_or(e, "target", "sprite_self"),
            **self._common_fields(e),
        )

    def _parse_escape(self, e: dict) -> EscapeOp:
        return EscapeOp(
            target=self._str_or(e, "target", "sprite_self"),
            inherit=self._bool_or(e, "inherit", False),
            urgent=self._bool_or(e, "urgent", False),
            then=self._parse_then(e),
            **self._common_fields(e),
        )

    def _parse_return(self, e: dict) -> ReturnOp:
        return ReturnOp(
            target=self._str_or(e, "target", "sprite_self"),
            **self._common_fields(e),
        )

    def _parse_lock(self, e: dict) -> LockOp:
        return LockOp(
            target=self._str_or(e, "target", "sprite_self"),
            turns=self._int_or(e, "turns", 1),
            **self._common_fields(e),
        )

    def _parse_interrupt(self, e: dict) -> InterruptOp:
        return InterruptOp(
            target=self._str_or(e, "target", "sprite_self"),
            **self._common_fields(e),
        )

    def _parse_exchange(self, e: dict) -> ExchangeOp:
        return ExchangeOp(
            what=self._str_or(e, "what", ""),
            **self._common_fields(e),
        )

    def _parse_reset(self, e: dict) -> ResetOp:
        return ResetOp(
            target=self._str_or(e, "target", "sprite_self"),
            stat=self._str_or(e, "stat", ""),
            **self._common_fields(e),
        )

    def _parse_redirect(self, e: dict) -> RedirectOp:
        return RedirectOp(
            target=self._str_or(e, "target", "sprite_self"),
            **self._common_fields(e),
        )

    def _parse_replay(self, e: dict) -> ReplayOp:
        from_ = e.get("from") or e.get("from_", "")
        return ReplayOp(
            from_=str(from_),
            skill_filter=e.get("skill_filter"),
            what=self._str_or(e, "what", ""),
            **self._common_fields(e),
        )

    def _parse_borrow(self, e: dict) -> BorrowOp:
        from_ = e.get("from") or e.get("from_", "")
        return BorrowOp(
            from_=str(from_),
            **self._common_fields(e),
        )

    def _parse_gain_skills(self, e: dict) -> GainSkills:
        return GainSkills(
            count=self._int_or(e, "count", 1),
            exclude_carried=self._bool_or(e, "exclude_carried", True),
            source=self._str_or(e, "source", "learnset"),
            target=self._str_or(e, "target", "sprite_self"),
            **self._common_fields(e),
        )

    def _parse_count(self, e: dict) -> CountOp:
        when = None
        if "when" in e:
            when = self._parse_condition(e["when"])
        return CountOp(
            name=self._str_or(e, "name", ""),
            when=when,
            then=self._parse_then(e),
            scope=self._str_or(e, "scope", "persistent"),
            **self._common_fields(e),
        )

    # Damage is a no-op marker in the engine — parse it as a ModOp placeholder
    # or skip it. For now, treat it as a no-op marker that gets filtered later.
    def _parse_damage(self, e: dict) -> SkillIROp:
        # "damage" opcode is a declarative marker (handled implicitly by engine)
        # Return a minimal ModOp that does nothing
        return ModOp(
            target="sprite_self",
            stat="",
            value=Literal(value=0),
            **self._common_fields(e),
        )

    # ── RISC IR opcode parsers (compatibility shim: RISC JSON → internal IR) ──

    def _parse_stat_stage(self, e: dict) -> StatStageOp:
        """RISC: stat_stage → StatStageOp (stage changes only, 1 step = 10%)."""
        stat = self._str_or(e, "stat", "")
        raw_steps = e.get("steps", 0)
        value = None
        steps = 0
        if isinstance(raw_steps, dict) and "q" in raw_steps:
            value = self._parse_value(raw_steps)
        else:
            steps = int(raw_steps)  # stat_stage steps are always integer
        return StatStageOp(
            target=self._str_or(e, "target", "sprite_self"),
            stat=stat,
            steps=steps,
            value=value,
            per_hit=self._bool_or(e, "per_hit", False),
            scope=self._str_or(e, "scope", "battlefield"),
            source=e.get("source"),
            **self._common_fields(e),
        )

    def _parse_power_mod(self, e: dict) -> PowerModOp:
        """RISC: power_mod → PowerModOp."""
        attr = self._str_or(e, "attr", "")
        delta = self._parse_value_optional(e, "delta")
        # Only parse "value" if explicitly present — _parse_value_optional
        # returns Literal(0) for missing keys, which shadows delta for
        # JSON that only provides delta (the majority case).
        value = self._parse_value(e["value"]) if "value" in e else None
        mode = self._str_or(e, "mode", "add")
        if delta is None and value is None:
            delta_val = e.get("delta", 0)
            if isinstance(delta_val, dict):
                delta = self._parse_value(delta_val)
            else:
                delta = Literal(value=int(delta_val))
        return PowerModOp(
            target=self._str_or(e, "target", "sprite_self"),
            attr=attr,
            delta=delta,
            value=value,
            mode=mode,
            per_hit=self._bool_or(e, "per_hit", False),
            scope=self._str_or(e, "scope", "battlefield"),
            skill_where=e.get("skill_where"),
            skill_filter=e.get("skill_filter"),
            element=e.get("element"),
            ttl=self._int_or(e, "ttl", 0),
            source=e.get("source") or e.get("name"),
            **self._common_fields(e),
        )

    def _parse_mult_mod(self, e: dict) -> MultModOp:
        """RISC: mult_mod → MultModOp."""
        attr = self._str_or(e, "attr", "")
        value = self._parse_value_optional(e, "value")
        if value is None:
            val = e.get("value", 1.0)
            value = Literal(value=float(val))
        return MultModOp(
            target=self._str_or(e, "target", "sprite_self"),
            attr=attr,
            value=value,
            mode=self._str_or(e, "mode", "set"),
            per_hit=self._bool_or(e, "per_hit", False),
            scope=self._str_or(e, "scope", "battlefield"),
            skill_where=e.get("skill_where"),
            skill_filter=e.get("skill_filter"),
            element=e.get("element"),
            source=e.get("source") or e.get("name"),
            on_next=self._bool_or(e, "on_next", False),
            if_type=e.get("if_type"),
            **self._common_fields(e),
        )

    def _parse_flag_set(self, e: dict) -> FlagSetOp:
        """RISC: flag_set → FlagSetOp."""
        flag = self._str_or(e, "flag", "")
        value = self._parse_value_optional(e, "value")
        if value is None:
            val = e.get("value", True)
            value = Literal(value=val)
        return FlagSetOp(
            target=self._str_or(e, "target", "sprite_self"),
            flag=flag,
            value=value,
            scope=self._str_or(e, "scope", "battlefield"),
            name=e.get("name"),
            source=e.get("source"),
            **self._common_fields(e),
        )

    def _parse_heal(self, e: dict) -> HealOp:
        """RISC: heal → HealOp."""
        ratio = None
        value = None
        if "ratio" in e:
            ratio = e["ratio"]
        elif "value" in e:
            value = self._parse_value(e["value"])
        else:
            ratio = 0.5
        return HealOp(
            target=self._str_or(e, "target", "sprite_self"),
            ratio=ratio,
            value=value,
            **self._common_fields(e),
        )

    def _parse_energize(self, e: dict) -> EnergizeOp:
        """RISC: energize → EnergizeOp."""
        delta = self._parse_value_optional(e, "delta")
        if delta is None:
            d = e.get("delta", 0)
            if isinstance(d, dict):
                delta = self._parse_value(d)
            else:
                delta = Literal(value=int(d))
        return EnergizeOp(
            target=self._str_or(e, "target", "sprite_self"),
            delta=delta,
            **self._common_fields(e),
        )

    def _parse_revive(self, e: dict) -> ReviveOp:
        """RISC: revive → ReviveOp."""
        hp_ratio = self._parse_value_optional(e, "hp_ratio")
        if hp_ratio is None:
            r = e.get("hp_ratio", 1.0)
            hp_ratio = Literal(value=float(r))
        return ReviveOp(
            target=self._str_or(e, "target", "sprite_self"),
            hp_ratio=hp_ratio,
            **self._common_fields(e),
        )

    def _parse_observer(self, e: dict) -> CountOp:
        """RISC: observer → CountOp (persistent condition→action binding)."""
        cond = None
        if "cond" in e:
            cond = self._parse_condition(e["cond"])
        counter = e.get("counter")
        name = ""
        threshold = 1
        reset_on_fire = True
        if counter and isinstance(counter, dict):
            name = counter.get("name", "")
            threshold = counter.get("threshold", 1)
            reset_on_fire = counter.get("reset", True)
        return CountOp(
            name=name,
            when=cond,
            then=self._parse_then(e),
            scope=self._str_or(e, "scope", "persistent"),
            threshold=threshold,
            reset_on_fire=reset_on_fire,
            **self._common_fields(e),
        )

    def _parse_defer(self, e: dict) -> Schedule:
        """RISC: defer → Schedule (delayed execution)."""
        then_effects = []
        then_list = e.get("then", [])
        for item in then_list:
            then_effects.append(self._parse_effect(item))
        return Schedule(
            turns=self._int_or(e, "turns", 0),
            at=e.get("at", "turn_start"),
            then=tuple(then_effects),
            **self._common_fields(e),
        )

    def _parse_inherit(self, e: dict) -> InheritEffects:
        """RISC: inherit → InheritEffects (pass effects to incoming sprite)."""
        effects = []
        eff_list = e.get("effects", [])
        for item in eff_list:
            effects.append(self._parse_effect(item))
        return InheritEffects(
            source=e.get("source", "self"),
            inherit_target=e.get("target", "enemy_new"),
            effects=tuple(effects),
            scope=self._str_or(e, "scope", "battlefield"),
            via_pending=self._bool_or(e, "via_pending", False),
            inherit_stat_effects=self._bool_or(e, "inherit_stat_effects", False),
            **self._common_fields(e),
        )

    def _parse_branch(self, e: dict) -> WhenBlock:
        """RISC: branch → WhenBlock (conditional branch)."""
        cond = self._parse_condition(e["cond"])
        then = tuple(self._parse_effect(item) for item in e.get("then", []))
        else_ = tuple(self._parse_effect(item) for item in e.get("else", []))
        elif_list = e.get("else_if", [])
        elif_ = tuple(
            WhenBranch(
                cond=self._parse_condition(branch["cond"]),
                then=tuple(self._parse_effect(item) for item in branch.get("then", [])),
            )
            for branch in elif_list
        )
        return WhenBlock(
            cond=cond,
            then=then,
            else_=else_,
            elif_=elif_,
            **self._common_fields(e),
        )

    # ── Engine-level ops (lives / transform / team_counter / trait_interaction) ──

    def _parse_lives(self, e: dict) -> LivesChange:
        return LivesChange(
            target_team=self._str_or(e, "target_team", "own"),
            delta=self._int_or(e, "delta", 1),
            **self._common_fields(e),
        )

    _parse_lives_change = _parse_lives

    def _parse_team_counter_write(self, e: dict) -> TeamCounterWrite:
        return TeamCounterWrite(
            target=self._str_or(e, "target", "own"),
            key=self._str_or(e, "key", ""),
            delta=self._int_or(e, "delta", 1),
            **self._common_fields(e),
        )

    def _parse_transform(self, e: dict) -> Transform:
        skills = e.get("skills")
        return Transform(
            species=self._str_or(e, "species", ""),
            skills=tuple(skills) if skills else None,
            reset_hp=self._bool_or(e, "reset_hp", False),
            reset_energy=self._bool_or(e, "reset_energy", False),
            **self._common_fields(e),
        )

    def _parse_trait_interaction(self, e: dict) -> TraitInteraction:
        return TraitInteraction(
            action=self._str_or(e, "action", ""),
            target=self._str_or(e, "target", "sprite_self"),
            copy_from=e.get("copy_from"),
            new_ability=e.get("new_ability"),
            **self._common_fields(e),
        )
