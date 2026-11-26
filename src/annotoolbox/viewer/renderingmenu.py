"""The viewer's View > Rendering menu: for the open game the look of its own shaders (the default where its compiler
and backend are there), our plain look beside it (the fallback), the backend the look needs (picked by the look, overridable), the
frame's parts as switches, the rendering quality (117 and 1800), and what draws (compiler, game build, adapter); a
look or part that cannot draw is greyed out with the reason. The choice is kept per game in ``Settings.rendering``
and ``Settings.quality``.

``CRenderingMenu(window, menu)`` is the entry; ``apply()`` after a game opens. The logic is ``render.nativelook``.
docs/viewer.md, "The renderer".
"""

from __future__ import annotations

from PySide6.QtGui import QAction, QActionGroup

from ..infra.i18n import tr
from ..render import nativelook, quality, rhiview
from ..suite import qualitydialog


def toggle_action(menu, text: str, checked: bool, on_toggled) -> QAction:
    """A checkable menu entry that tells ``on_toggled`` its new state (``addAction(text, slot)`` calls the slot
    without it)."""
    action = menu.addAction(text)
    action.setCheckable(True)
    action.setChecked(checked)
    action.toggled.connect(on_toggled)
    return action


class CRenderingMenu:
    """The menu of one viewer window (module docstring); ``window`` is the ``CViewerWindow``."""

    def __init__(self, window, menu) -> None:
        self.m_window, self.m_menu = window, menu
        self._m_found: dict[str, nativelook.SFound] = {}
        self._m_builds: dict[str, str] = {}
        self._m_backends: set[str] | None = None
        menu.aboutToShow.connect(self.rebuild)

    # what the menu shows
    def game(self) -> str:
        return getattr(self.m_window.m_tree, "m_game", "") if self.m_window.m_tree is not None else ""

    def prefetch(self, game: str, folder) -> None:
        """Look for the game's compiler and read its build, once per game (about 1.8 s for 117): the thread that
        opens the game calls it, so the window's first ``options`` finds both."""
        if game not in self._m_found:
            self._m_builds[game] = nativelook.exe_version(folder)  # first: ``options`` reads it after ``_m_found``
            self._m_found[game] = nativelook.find_compiler(game, folder)

    def backends(self) -> set[str]:
        """The backends this window can draw with (probed once)."""
        if self._m_backends is None:
            self._m_backends = {
                rhiview.composable(backend, self.m_window.m_is_gl_host) for backend in rhiview.offered()
            }
        return self._m_backends

    def options(self) -> nativelook.SOptions:
        game = self.game()
        folder = getattr(self.m_window.m_tree, "m_folder", None) if self.m_window.m_tree is not None else None
        self.prefetch(game, folder)
        choice = nativelook.SChoice.of(self.m_window.m_settings.rendering, game)
        default = rhiview.chosen(self.m_window.m_settings.renderer)
        return nativelook.options(game, self._m_found[game], self.backends(), choice, default, self._m_builds[game])

    # acting on it
    def apply(self) -> None:
        """The open game's look, switches and backend into the view (after a game opened)."""
        opts = self.options()
        self.m_window.set_state_quietly(
            look=opts.look, switches={feature.key: feature.on for feature in opts.features}, quality=self.quality()
        )
        if opts.backend != self.m_window.m_model.m_backend:
            # another API than the window's is kept for the next start (a window keeps the API it opened with)
            other = rhiview.api_of(opts.backend) != rhiview.api_of(self.m_window.m_model.m_backend)
            self.m_window.set_renderer(opts.backend, remember=other)

    def quality(self) -> quality.SQuality | None:
        """The open game's rendering quality as the settings keep it (117 and 1800; None for the others: the best);
        the game's level values are vanilla's (the viewer uses none of them, quality.md "What 1800's path needs")."""
        game = self.game()
        if game not in quality.GAMES:
            return None
        return quality.resolve(game, (getattr(self.m_window.m_settings, "quality", None) or {}).get(game))

    def set_quality(self, key: str) -> None:
        """The open game drawn as the menu's entry ``key`` sets it from now on (Custom: as its window sets it), kept
        per game in the settings (as the island editor's Rendering > Rendering quality)."""
        game = self.game()
        if game not in quality.GAMES:
            return
        is_set, stored = qualitydialog.choose(self.m_window, game, key, self.quality())
        if is_set:
            stored_by_game = getattr(self.m_window.m_settings, "quality", None)
            self.m_window.m_settings.quality = qualitydialog.replace_game(stored_by_game, game, stored)
            self.apply()

    def _quality_menu(self, parent_menu) -> None:
        """View > Rendering > Rendering quality for 117 and 1800: the game's own settings, the presets and Custom."""
        current_quality = self.quality()
        if current_quality is None:
            return
        menu = parent_menu.addMenu(tr("islands.menu.rendering_quality"))
        menu.setToolTip(tr("islands.tip.how_much_view_draws_117"))
        qualitydialog.check_actions(qualitydialog.add_actions(menu, self.set_quality), current_quality)

    def _choose(self, **change) -> None:
        game = self.game()
        choice = nativelook.SChoice.of(self.m_window.m_settings.rendering, game)
        for key, value in change.items():
            if key == "feature":
                choice.features[value[0]] = value[1]
            else:
                setattr(choice, key, value)
        choice.store(self.m_window.m_settings.rendering, game)
        self.apply()

    def rebuild(self) -> None:
        """The menu's entries for the open game, made each time it opens."""
        root_menu = self.m_menu
        root_menu.clear()
        opts = self.options()
        group = QActionGroup(root_menu, exclusive=True)
        for look in opts.looks:
            text = look.label if look.enabled else tr("viewer.label_why", label=look.label, why=look.why)
            action = root_menu.addAction(text, lambda k=look.key: self._choose(look=k))
            action.setCheckable(True)
            action.setChecked(look.key == opts.look)
            action.setEnabled(look.enabled)
            action.setToolTip(look.why)
            group.addAction(action)
        root_menu.addSeparator()
        backends = root_menu.addMenu(
            tr("viewer.menu.backend_name", name=rhiview.LABELS.get(opts.backend, opts.backend))
        )
        auto = backends.addAction(
            tr("viewer.automatic_name", name=rhiview.LABELS.get(opts.needed, opts.needed)),
            lambda: self._choose(backend=""),
        )
        auto.setCheckable(True)
        chosen = nativelook.SChoice.of(self.m_window.m_settings.rendering, self.game()).backend
        auto.setChecked(not chosen)
        for key, text in rhiview.LABELS.items():
            usable = key in self.backends()
            why = "" if usable else rhiview.blocked(key) or tr("common.needs_backend_cannot_draw", backend=text)
            action = backends.addAction(tr("viewer.label_why", label=text, why=why) if why else text,
                                     lambda k=key: self._choose(backend=k))  # fmt: skip
            action.setCheckable(True)
            action.setChecked(key == chosen)
            action.setEnabled(usable)
            action.setToolTip(why)
        self._quality_menu(root_menu)
        root_menu.addSeparator()
        for feature in opts.features:
            text = (
                tr(feature.label)
                if feature.enabled
                else tr("viewer.label_status", label=tr(feature.label), status=feature.status_text)
            )
            action = toggle_action(
                root_menu,
                text,
                feature.enabled and feature.on,
                lambda is_on, k=feature.key: self._choose(feature=(k, is_on)),
            )
            action.setEnabled(feature.enabled)
        root_menu.addSeparator()
        for line in self.info(opts):
            root_menu.addAction(line).setEnabled(False)
        toggle_action(
            root_menu, tr("viewer.hardware_acceleration_next_start"), self.m_window.m_settings.hardware, self._hardware
        )

    def info(self, opts: nativelook.SOptions) -> list[str]:
        lines = [
            tr("viewer.compiler_name", name=opts.compiler or "-"),
            tr("viewer.game_build_name", name=opts.build or "-"),
        ]
        if not nativelook.direct3d_here():
            lines.append(tr("viewer.direct3d_module_off", why=tr("common.direct3d_12_windows_only")))
        ctx = getattr(self.m_window.m_model, "m_ctx", None)
        if ctx is not None and hasattr(ctx, "m_rhi"):
            adapter = nativelook.adapter_of(ctx.m_rhi)
            support = nativelook.gpu_support()
            lines.append(tr("viewer.adapter_support", adapter=adapter.text(), support=support))
        else:
            lines.append(
                tr(
                    "viewer.adapter_name",
                    name=rhiview.LABELS.get(self.m_window.m_model.m_backend, self.m_window.m_model.m_backend),
                )
            )
        return lines

    def _hardware(self, is_on: bool) -> None:
        self.m_window.m_settings.hardware = is_on
        self.m_window.statusBar().showMessage(
            tr("viewer.hardware_acceleration_on_next_start")
            if is_on
            else tr("viewer.hardware_acceleration_off_software"),
            8000,
        )
