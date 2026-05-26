"""TraitToObserver — compile trait JSON effects[] into Observer params.

Converts each {op: observer, cond, then, scope, listen, counter} entry
into a dict that TraitLoader can construct an Observer from.

This module MUST NOT import from backend.engine to avoid circular imports.
"""

from __future__ import annotations

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
        then = effect.get("then", [])
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
        }
