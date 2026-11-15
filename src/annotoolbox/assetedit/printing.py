"""Printing a page of ``report`` (HTML): to a printer through the system's print dialog, or into a PDF file.

``print_html(parent, html, pdf=None)`` prints; with ``pdf`` it writes that file and asks nothing (a test, "Print
to PDF"). Returns whether anything was printed.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QTextDocument
from PySide6.QtPrintSupport import QPrintDialog, QPrinter


def print_html(parent, html: str, pdf: Path | None = None) -> bool:
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    if pdf is not None:
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(str(pdf))
    elif QPrintDialog(printer, parent).exec() != QPrintDialog.DialogCode.Accepted:
        return False
    page = QTextDocument()
    page.setHtml(html)
    page.print_(printer)
    return True
