"""TraitToObserver — compile trait JSON effects[] into Observer params.

Converts each {op: observer, cond, then, scope, listen, counter} entry
into a dict that TraitLoader can construct an Observer from.

This module MUST NOT import from backend.engine to avoid circular imports.
"""

from __future__ import annotations

from copy import deepcopy

from backend.vm.cond import infer_triggers


class TraitToObserver:
    """Compile a trait's effects[] array into Observer parameter dicts."""

    def compile(self, effects: list[dict]) -> list[dict]:
        """Convert a list of effect dicts to Observer param dicts.

        Each effect with op='observer' becomes one Observer spec dict.
        Non-observer effects are skipped.
        """
        results: list[dict] = []
        for effect in effects:
            if not isinstance(effect, dict):
                continue
            if effect.get("op") != "observer":
                continue
            results.append(self._compile_one(effect))
        return results

    def _compile_one(self, effect: dict) -> dict:
        cond = effect.get("cond", {})
        # `then` 必须**拷贝**：注册时 `ObserverRegistry._index` 会往效果树里原地注入
        # source/scope（`_bake_inject_*` 是原地改）。直接引用的话改的是**共享的 trait JSON**，
        # 第二个持有者（或任何读原始数据的地方）看到的就是被注入过的树
        # —— 今天幂等（source/scope 都取自同一份 JSON），但这是随时会烂的耦合（2026-09-23 审计）。
        then = deepcopy(effect.get("then", []))
        scope = effect.get("scope", "persistent")
        source = effect.get("source", "")
        name = effect.get("name", "")

        # listen: explicit or inferred from cond
        listen = effect.get("listen")
        if listen is None:
            listen = infer_triggers(cond)
        elif isinstance(listen, str):
            listen = frozenset({listen})
        elif isinstance(listen, list):
            listen = frozenset(listen)
        else:
            listen = frozenset()

        # counter (threshold counting)
        counter = effect.get("counter")
        threshold = 1
        reset_on_fire = True
        if isinstance(counter, dict):
            name = counter.get("name", name)
            threshold = counter.get("threshold", 1)
            reset_on_fire = counter.get("reset", True)

        return {
            "cond": cond,
            "then": then,
            "scope": scope,
            "name": name,
            "source": source,
            "listen": listen,
            "threshold": threshold,
            "reset_on_fire": reset_on_fire,
            "reset": effect.get("reset", ""),
            "once": bool(effect.get("once", False)),  # 每场战斗仅触发一次（安眠 等）
        }
