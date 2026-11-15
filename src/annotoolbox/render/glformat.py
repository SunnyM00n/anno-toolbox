"""The OpenGL format of the suite's 3D views: 3.3 core with a depth buffer (the renderers multisample themselves).

``prepare()`` sets it as the default before the program's first window; ``surface_format()`` is the format itself.
"""

from __future__ import annotations


def surface_format():
    """OpenGL 3.3 core with depth, as a ``QSurfaceFormat``."""
    from PySide6.QtGui import QSurfaceFormat

    fmt = QSurfaceFormat()
    fmt.setVersion(3, 3)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    fmt.setDepthBufferSize(24)
    return fmt


def prepare() -> None:
    """Make it the default; call it before the ``QApplication`` exists."""
    from PySide6.QtGui import QSurfaceFormat

    QSurfaceFormat.setDefaultFormat(surface_format())
