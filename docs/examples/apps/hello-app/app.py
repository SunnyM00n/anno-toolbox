"""A small Python app for the Toolbox: a page that lists the games' folders and counts the files of one game.

To try it, copy this folder into the Toolbox's apps folder (Manage apps > Open the apps folder).
Start the Toolbox again, and "Hello app" appears in the app switcher.
docs/app-api.md explains every line.
"""

from annotoolbox.suite.appapi import API_VERSION, AppContext, ExternalApp


def make(context: AppContext):
    from PySide6.QtWidgets import QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget

    class HelloPage(QWidget):
        is_counting = False

        def can_close(self) -> bool:
            # The Toolbox asks every page before it closes any: a False keeps the Toolbox open (docs/app-api.md).
            # A page without this method agrees.
            if not self.is_counting:
                return True
            answer = QMessageBox.question(self, "Hello app", "A count is still running. Close anyway?")
            return answer == QMessageBox.StandardButton.Yes

    page = HelloPage()
    layout = QVBoxLayout(page)
    folders = {game: context.game_dir(game) for game in ("anno117", "anno2070", "anno1800")}
    layout.addWidget(QLabel("\n".join(f"{game}: {folder or '(not set)'}" for game, folder in folders.items())))
    layout.addWidget(QLabel(f"Project folder: {context.project_folder}"))
    result = QLabel()
    button = QPushButton("Count the files of Anno 117")

    def counted(tree):  # runs on the window's thread again
        page.is_counting = False
        if isinstance(tree, Exception):
            result.setText(str(tree))
        else:
            result.setText(f"{len(tree.m_paths)} files")
            context.say("Counted.")

    def count():
        # Opening a game indexes its archives.
        # The shared runner does that off the window's thread.
        page.is_counting = True
        context.background.run(lambda: context.open_game("anno117"), counted)

    button.clicked.connect(lambda _checked=False: count())
    layout.addWidget(button)
    layout.addWidget(result)
    return page


def register() -> ExternalApp:
    return ExternalApp(
        key="hello-app",
        title="Hello app",
        summary="An example app of the Toolbox's app API",
        api_version=API_VERSION,
        make=make,
        games=(),
    )
