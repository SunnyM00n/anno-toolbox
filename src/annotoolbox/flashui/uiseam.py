"""The Flash UI editor's extension points: the one place that asks what other code adds to the editor.

``register(point, name, value)`` adds an entry, ``extensions(point)`` lists them in the order registered. The
points: ``actions`` - a menu entry of the window, the value a ``(label, callable(window))`` pair. The editor
works without any entry; nothing here imports the code that registers one.
"""

from __future__ import annotations

POINTS = ("actions",)
_REGISTRY: dict[str, dict[str, object]] = {point: {} for point in POINTS}


def register(point: str, name: str, value: object) -> None:
    if point not in _REGISTRY:
        raise KeyError(f"no extension point {point!r} (the points: {', '.join(POINTS)})")
    _REGISTRY[point][name] = value


def unregister(point: str, name: str) -> None:
    _REGISTRY.get(point, {}).pop(name, None)


def extensions(point: str) -> dict[str, object]:
    return dict(_REGISTRY.get(point, {}))
