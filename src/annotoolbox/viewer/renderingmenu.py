"""The viewer's View > Rendering menu: for the open game the look of its own shaders (the default where its compiler
and backend are there), our plain look beside it (the fallback), the backend the look needs (picked by the look, overridable), the
frame's parts as switches, the rendering quality (117 and 1800), and what draws (compiler, game build, adapter); a
look or part that cannot draw is greyed out with the reason. The choice is kept per game in ``Settings.rendering``
and ``Settings.quality``.

``RenderingMenu(window, menu)`` is the entry; ``apply()`` after a game opens. The logic is ``render.nativelook``.
docs/viewer.md, "The renderer".
"""

from __future__ import annotations

from PySide6.QtGui import QAction, QActionGroup

from ..infra.i18n import N_, tr
from ..render import nativelook, quality, rhiview
from ..suite import widgets

BACKENDS = {"gl": "OpenGL (moderngl)", "d3d12": "Direct3D 12", "d3d11": "Direct3D 11", "vulkan": "Vulkan",
            "opengl": "OpenGL (Qt's QRhi)"}  # fmt: skip


def toggle_action(menu, text: str, checked: bool, on_toggled) -> QAction:
    """A checkable menu entry that tells ``on_toggled`` its new state (``addAction(text, slot)`` calls the slot
    without it)."""
    act = menu.addAction(text)
    act.setCheckable(True)
    act.setChecked(checked)
    act.toggled.connect(on_toggled)
    return act


class RenderingMenu:
    """The menu of one viewer window (module docstring); ``window`` is the ``ViewerWindow``."""

    def __init__(self, window, menu) -> None:
        self.w, self.menu = window, menu
        self._found: dict[str, nativelook.Found] = {}
        self._builds: dict[str, str] = {}
        self._backends: set[str] | None = None
        menu.aboutToShow.connect(self.rebuild)

    # what the menu shows
    def game(self) -> str:
        return getattr(self.w.tree, "game", "") if self.w.tree is not None else ""

    def backends(self) -> set[str]:
        """The backends this window can draw with (probed once)."""
        if self._backends is None:
            found = set() if rhiview.blocked("gl") else {"gl"}
            for name in ("d3d12", "d3d11", "vulkan", "opengl"):
                try:
                    if rhiview.available(name):
                        found.add(name)
                except Exception:  # a backend that cannot even be probed is not there
                    continue
            self._backends = {rhiview.composable(b, self.w.hosts_gl) for b in found}
        return self._backends

    def options(self) -> nativelook.Options:
        game = self.game()
        folder = getattr(self.w.tree, "folder", None) if self.w.tree is not None else None
        if game not in self._found:
            self._found[game] = nativelook.find_compiler(game, folder)
            self._builds[game] = nativelook.exe_version(folder)
        choice = nativelook.Choice.of(self.w.settings.rendering, game)
        default = rhiview.chosen(self.w.settings.renderer)
        return nativelook.options(game, self._found[game], self.backends(), choice, default, self._builds[game])

    # acting on it
    def apply(self) -> None:
        """The open game's look, switches and backend into the view (after a game opened)."""
        opts = self.options()
        self.w.set_state_quietly(look=opts.look, switches={f.key: f.on for f in opts.features}, quality=self.quality())
        if opts.backend != self.w.model.backend:
            # another API than the window's is kept for the next start (a window keeps the API it opened with)
            other = rhiview.api_of(opts.backend) != rhiview.api_of(self.w.model.backend)
            self.w.set_renderer(opts.backend, remember=other)

    def quality(self) -> quality.Quality | None:
        """The open game's rendering quality as the settings keep it (117 and 1800; None for the others: the best);
        the game's level values are vanilla's (the viewer uses none of them, quality.md "What 1800's path needs")."""
        game = self.game()
        if game not in quality.GAMES:
            return None
        return quality.resolve(game, (getattr(self.w.settings, "quality", None) or {}).get(game))

    def set_quality(self, name: str) -> None:
        """The open game drawn at preset ``name`` from now on, kept per game in the settings (as the island
        editor's Rendering > Rendering quality)."""
        game = self.game()
        if game not in quality.GAMES:
            return
        kept = dict(getattr(self.w.settings, "quality", None) or {})
        kept[game] = name
        self.w.settings.quality = kept
        self.apply()

    def _quality_menu(self, m) -> None:
        """View > Rendering > Rendering quality for 117 and 1800 (Custom only as the settings file sets it)."""
        q = self.quality()
        if q is None:
            return
        menu = m.addMenu(tr("islands.menu.rendering_quality"))
        menu.setToolTip(tr("islands.tip.how_much_view_draws_117"))
        group = QActionGroup(menu, exclusive=True)
        for key, label in (*quality.PRESETS.items(), (quality.CUSTOM, N_("islands.custom"))):
            act = menu.addAction(tr(label), lambda k=key: self.set_quality(k))
            act.setCheckable(True)
            act.setChecked(q.preset == key)
            widgets.enable_unless(act, tr("islands.tip.quality_custom") if key == quality.CUSTOM else "")
            group.addAction(act)

    def _choose(self, **change) -> None:
        game = self.game()
        choice = nativelook.Choice.of(self.w.settings.rendering, game)
        for key, value in change.items():
            if key == "feature":
                choice.features[value[0]] = value[1]
            else:
                setattr(choice, key, value)
        choice.store(self.w.settings.rendering, game)
        self.apply()

    def rebuild(self) -> None:
        """The menu's entries for the open game, made each time it opens."""
        m = self.menu
        m.clear()
        opts = self.options()
        group = QActionGroup(m, exclusive=True)
        for look in opts.looks:
            text = look.label if look.enabled else tr("viewer.label_why", label=look.label, why=look.why)
            act = m.addAction(text, lambda k=look.key: self._choose(look=k))
            act.setCheckable(True)
            act.setChecked(look.key == opts.look)
            act.setEnabled(look.enabled)
            act.setToolTip(look.why)
            group.addAction(act)
        m.addSeparator()
        backends = m.addMenu(tr("viewer.menu.backend_name", name=BACKENDS.get(opts.backend, opts.backend)))
        auto = backends.addAction(
            tr("viewer.automatic_name", name=BACKENDS.get(opts.needed, opts.needed)), lambda: self._choose(backend="")
        )
        auto.setCheckable(True)
        chosen = nativelook.Choice.of(self.w.settings.rendering, self.game()).backend
        auto.setChecked(not chosen)
        for key, text in BACKENDS.items():
            usable = key in self.backends()
            why = "" if usable else rhiview.blocked(key) or tr("common.needs_backend_cannot_draw", backend=text)
            act = backends.addAction(tr("viewer.label_why", label=text, why=why) if why else text,
                                     lambda k=key: self._choose(backend=k))  # fmt: skip
            act.setCheckable(True)
            act.setChecked(key == chosen)
            act.setEnabled(usable)
            act.setToolTip(why)
        self._quality_menu(m)
        m.addSeparator()
        for f in opts.features:
            text = tr(f.label) if f.enabled else tr("viewer.label_status", label=tr(f.label), status=f.status_text)
            act = toggle_action(m, text, f.enabled and f.on, lambda on, k=f.key: self._choose(feature=(k, on)))
            act.setEnabled(f.enabled)
        m.addSeparator()
        for line in self.info(opts):
            m.addAction(line).setEnabled(False)
        toggle_action(m, tr("viewer.hardware_acceleration_next_start"), self.w.settings.hardware, self._hardware)

    def info(self, opts: nativelook.Options) -> list[str]:
        lines = [
            tr("viewer.compiler_name", name=opts.compiler or "-"),
            tr("viewer.game_build_name", name=opts.build or "-"),
        ]
        if not nativelook.direct3d_here():
            lines.append(tr("viewer.direct3d_module_off", why=tr("common.direct3d_12_windows_only")))
        ctx = getattr(self.w.model, "ctx", None)
        if ctx is not None and hasattr(ctx, "rhi"):
            adapter = nativelook.adapter_of(ctx.rhi)
            support = nativelook.gpu_support()
            lines.append(tr("viewer.adapter_support", adapter=adapter.text(), support=support))
        else:
            lines.append(tr("viewer.adapter_name", name=BACKENDS.get(self.w.model.backend, self.w.model.backend)))
        return lines

    def _hardware(self, on: bool) -> None:
        self.w.settings.hardware = on
        self.w.statusBar().showMessage(
            tr("viewer.hardware_acceleration_on_next_start") if on else tr("viewer.hardware_acceleration_off_software"),
            8000,
        )
