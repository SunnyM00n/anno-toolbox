"""The Converter app of the suite (docs/converter-app.md): single files, assets and islands of the games turned into other
formats, each entry a command of the command line run in the window with its options as a form.

``catalogue`` lists the entries (``extspecs.SConversion``, the baseline's and what ``features.CONVERSIONS``
adds); ``commandform`` makes a command's form and its command line; ``runner`` runs it off the window's thread;
``gamefiles`` takes sources from an installed game; ``window`` is the app.
"""
