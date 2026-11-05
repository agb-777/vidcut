from __future__ import annotations

from typing import Any, Callable, List


class _Emitter:
    def __init__(self) -> None:
        self._slots: List[Callable[..., Any]] = []

    def connect(self, fn: Callable[..., Any]) -> None:
        self._slots.append(fn)

    def emit(self, *args: Any) -> None:
        for fn in list(self._slots):
            fn(*args)


class Signal:
    def __init__(self, *types: type) -> None:
        self.types = types
        self._attr = ""

    def __set_name__(self, owner: type, name: str) -> None:
        self._attr = "_signal_" + name

    def __get__(self, obj: Any, objtype: Any = None) -> Any:
        if obj is None:
            return self
        emitter = obj.__dict__.get(self._attr)
        if emitter is None:
            emitter = obj.__dict__[self._attr] = _Emitter()
        return emitter
