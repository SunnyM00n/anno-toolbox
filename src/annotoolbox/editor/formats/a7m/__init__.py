"""The new engine's islands in the editor: Anno 117 and Anno 1800 (``.a7m``) and Anno 2205's session maps (``.a6m``),
one FileDB format family read by ``...formats.a7mread``.

Start at ``reader``: an 1800 island read into what the 117 editor works on (``Island1800``, ``IslandFile1800``, the
install's files as ``GameFiles``); 2205's ``session`` builds on it. The editing of each game: ``edit117`` (vanilla 117
islands into a mod), ``edit1800`` (1800 islands into a mod that replaces them), ``edit2205`` (session maps into an
archive of their own); a 2205 session's ground: ``sessionground`` (heights, paint, rivers, lakes) and
``sessionpages`` (its normal-map pages after a height edit).
"""
