"""What one editing operation changed, as the views redraw it (``Change``), and the small helpers of the edit
document: the box that covers two boxes, a number as the edit file writes it, a copy of JSON data and a turn about
the vertical axis. Shared by ``editsession``, its ground edits (``sessionterrain``) and the views."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

from ..infra.i18n import N_, tr


@dataclass
class Change:
    """What one operation changed, for the view to redraw."""

    entries: set = field(default_factory=set)
    structure: bool = False  # entries added or taken out: batches change
    heights: tuple | None = None  # corner box x0, z0, x1, z1
    weights: tuple | None = None  # weight texel box x0, z0, x1, z1 (layers may have grown)
    water: bool = False
    forest: bool = False
    materials: bool = False


REPORT_LABELS = {  # a report's keys -> the words shown for them
    "objects changed": N_("islands.objects_changed"),
    "objects deleted": N_("islands.objects_deleted"),
    "objects added": N_("islands.objects_added"),
    "heights": N_("islands.heights"),
    "paint": N_("islands.paint_lower"),
    "trees planted": N_("islands.trees_planted"),
    "properties": N_("islands.properties"),
    "rivers": N_("islands.rivers_lower"),
    "lakes": N_("islands.lakes"),
    "coast lines": N_("islands.coast_lines"),
    "path cells": N_("islands.path_cells"),
}


def count_line(report: dict) -> str:
    """The counts of a build's report as one line ("heights 3, paint 1"): what is zero, and the unmatched names (listed
    apart), left out."""
    return ", ".join(
        f"{tr(REPORT_LABELS[k]) if k in REPORT_LABELS else k} {v}" for k, v in report.items() if v and k != "unmatched"
    )


def num(v):
    """A number as the edit file writes it: whole numbers without a fraction."""
    v = float(v)
    return int(v) if v.is_integer() and abs(v) < 2**53 else v


def union_box(a, b):
    """The box (x0, z0, x1, z1) round both; ``a`` may be None (nothing yet)."""
    if a is None:
        return b
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def copy_json(v):
    return None if v is None else json.loads(json.dumps(v))


def yaw_quat(yaw: float) -> list[float]:
    """A turn about the vertical axis as the view's turn makes it, in Anno's axes."""
    return [0.0, -math.sin(yaw / 2), 0.0, math.cos(yaw / 2)]
