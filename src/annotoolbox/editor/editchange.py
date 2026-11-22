"""What one editing operation changed, as the views redraw it (``SChange``), and the small helpers of the edit
document: the box that covers two boxes, a number as the edit file writes it, a copy of JSON data and a turn about
the vertical axis. Shared by ``editsession``, its ground edits (``sessionterrain``) and the views."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

from ..infra.i18n import N_, tr


@dataclass
class SChange:
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
        f"{tr(REPORT_LABELS[name]) if name in REPORT_LABELS else name} {count}"
        for name, count in report.items()
        if count and name != "unmatched"
    )


def num(value):
    """A number as the edit file writes it: whole numbers without a fraction."""
    value = float(value)
    return int(value) if value.is_integer() and abs(value) < 2**53 else value


def union_box(covered, added):
    """The box (x0, z0, x1, z1) round both; ``covered`` may be None (nothing yet)."""
    if covered is None:
        return added
    return (min(covered[0], added[0]), min(covered[1], added[1]), max(covered[2], added[2]), max(covered[3], added[3]))


def copy_json(data):
    return None if data is None else json.loads(json.dumps(data))


def yaw_quat(yaw_rad: float) -> list[float]:
    """A turn about the vertical axis as the view's turn makes it, in Anno's axes."""
    return [0.0, -math.sin(yaw_rad / 2), 0.0, math.cos(yaw_rad / 2)]
