"""What each field of an effect file does, as the particle editor's forms say it: how it is edited (a switch, a
number, a whole number, a closed list, a file name, text, read only) and a line for its tool tip. No Qt.

``info(syntax, name)`` is the entry (``syntax``: ``rdp``, ``xml`` for 2070's and 1404's ``.efc``, ``brace`` for
1701's); a field the tables do not name is text, said as "not described". The meanings are docs/particle-editor.md's
tables, which say where each one comes from.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..infra.i18n import N_

SWITCH, NUMBER, WHOLE, CHOICE, FILE, TEXT, FIXED = "switch", "number", "whole", "choice", "file", "text", "fixed"


@dataclass(frozen=True)
class SInfo:
    """How a field is edited and what it does (``meaning`` is marked for translation, ``tr`` it when shown)."""

    kind: str = TEXT
    meaning: str = ""
    choices: tuple[tuple[str, str], ...] = ()  # (value as written, its name)
    limits: tuple[float, float] | None = (
        None  # the least and the most a number may be (a value outside breaks the game)
    )


SINCE_ZERO = (0.0, math.inf)
BYTE = (0.0, 255.0)
ALIGNED = (
    ("0", N_("particles.turns_emitter")),
    ("1", N_("particles.faces_camera")),
    ("2", N_("particles.stands_upright")),
)
LINE_TYPES = (("0", N_("particles.a_quad")), ("1", N_("particles.a_ribbon")), ("2", N_("particles.ribbon_type_2")))
KINDS_2070 = (("1", N_("particles.particles")), ("2", N_("particles.a_mesh")), ("3", N_("particles.a_billboard")), ("4", N_("particles.a_line")),
              ("5", N_("particles.mass")), ("6", N_("particles.an_arrow")), ("7", N_("particles.a_flame")))  # fmt: skip
TECHNIQUES = (("0", N_("particles.alpha_blend")), ("1", N_("particles.additive")), ("2", N_("particles.refract")), ("3", N_("particles.depth_written")),
              ("4", N_("particles.no_depth_test")), ("5", N_("particles.waters_wave_map")))  # fmt: skip
ALIGNMENT_2070 = (("0", N_("particles.faces_camera")), ("1", N_("particles.upright")), ("2", N_("particles.fixed")))

RDP = {
    "FrameCount": SInfo(FIXED, N_("particles.frames_strip_tick_data_made")),
    "FrameTime": SInfo(NUMBER, N_("particles.milliseconds_frame_lasts_pace_whole"), (), (1.0, 1.0e6)),
    "Looped": SInfo(SWITCH, N_("particles.strip_repeats_off_plays_once")),
    "ParticleCount": SInfo(FIXED, N_("particles.particles_tick_data_holds")),
    "TotalTickCount": SInfo(FIXED, N_("particles.ticks_tick_data_holds")),
    "SelfOcclusion": SInfo(SWITCH, N_("particles.shaded_effects_centre")),
    "Lighting": SInfo(SWITCH, N_("particles.lit_sun")),
    "DoSort": SInfo(SWITCH, N_("particles.particles_drawn_far_near")),
    "Invincible": SInfo(SWITCH, N_("particles.kept_when_camera_far")),
    "MaxScale": SInfo(NUMBER, N_("particles.metres_full_scale_byte_means"), (), SINCE_ZERO),
    "Center": SInfo(NUMBER, N_("particles.centre_box_positions_fractions_moves")),
    "Extents": SInfo(NUMBER, N_("particles.half_size_box_stretches_cloud")),
    "TexturePath": SInfo(FILE, N_("particles.texture_every_material_shows_unless")),
    "NormalMapPath": SInfo(FILE, N_("particles.normal_map_particles")),
    "ViewAligned": SInfo(CHOICE, N_("particles.how_quad_turned"), ALIGNED),
    "Order": SInfo(WHOLE, N_("particles.draw_order_materials_0_255"), (), BYTE),
    "ColorBoost": SInfo(NUMBER, N_("particles.lifts_colour_1_plus_boost"), (), BYTE),
    "Additive": SInfo(SWITCH, N_("particles.adds_colour_off_blends_alpha")),
    "Refractive": SInfo(SWITCH, N_("particles.bends_what_behind")),
    "WaveParticle": SInfo(SWITCH, N_("particles.raises_oceans_waves_instead_drawing")),
    "OpaqueWithDepth": SInfo(SWITCH, N_("particles.cut_alpha_0_1_written")),
    "LineType": SInfo(CHOICE, N_("particles.quad_ribbon"), LINE_TYPES),
    "LineTiling": SInfo(NUMBER, N_("particles.how_often_ribbon_repeats_texture"), (), SINCE_ZERO),
    "LineLength": SInfo(NUMBER, N_("particles.ribbons_length"), (), SINCE_ZERO),
    "cropUVWH": SInfo(NUMBER, N_("particles.part_texture_shown_x_y")),
    "MovieTexPath": SInfo(FILE, N_("particles.materials_own_texture_flipbook_sheet")),
    "MovieNormPath": SInfo(FILE, N_("particles.flipbooks_normal_sheet")),
    "MovieMotionPath": SInfo(FILE, N_("particles.flipbooks_motion_sheet")),
    "MovieFPS": SInfo(NUMBER, N_("particles.frames_second_flipbook"), (), SINCE_ZERO),
}  # fmt: skip

XML = {
    "Name": SInfo(TEXT, N_("particles.emitters_name")),
    "EffectType": SInfo(CHOICE, N_("particles.what_emitter_draws"), KINDS_2070),
    "Texture0": SInfo(FILE, N_("particles.texture_atlas_tile_shows_part")),
    "Texture1": SInfo(FILE, N_("particles.second_texture_empty_every_file")),
    "Texture2": SInfo(FILE, N_("particles.third_texture_empty_every_file")),
    "Shader": SInfo(FILE, N_("particles.effect_shader")),
    "Model": SInfo(FILE, N_("particles.mesh_emitter_draws")),
    "Technique": SInfo(CHOICE, N_("particles.how_drawn"), TECHNIQUES),
    "Blending": SInfo(WHOLE, N_("particles.how_drawn_1404_0_1")),
    "Delay": SInfo(NUMBER, N_("particles.milliseconds_after_effect_starts"), (), SINCE_ZERO),
    "LifeTime": SInfo(NUMBER, N_("particles.milliseconds_particle_lives"), (), SINCE_ZERO),
    "Duration": SInfo(NUMBER, N_("particles.milliseconds_emitter_emits"), (), SINCE_ZERO),
    "Loop": SInfo(SWITCH, N_("particles.emitter_repeats")),
    "Bound": SInfo(SWITCH, N_("particles.particles_follow_emitter")),
    "OffsetX": SInfo(NUMBER, N_("particles.emitters_place_effect")),
    "OffsetY": SInfo(NUMBER, N_("particles.emitters_place_effect")),
    "OffsetZ": SInfo(NUMBER, N_("particles.emitters_place_effect")),
    "Alignment": SInfo(CHOICE, N_("particles.how_sprite_turned"), ALIGNMENT_2070),
    "WindDependency": SInfo(NUMBER, N_("particles.0_1_how_much_wind"), (), (0.0, 1.0)),
    "AtlasOffsetU": SInfo(NUMBER, N_("particles.one_corner_textures_tile")),
    "AtlasOffsetV": SInfo(NUMBER, N_("particles.one_corner_textures_tile")),
    "AtlasTilingU": SInfo(NUMBER, N_("particles.opposite_corner_textures_tile")),
    "AtlasTilingV": SInfo(NUMBER, N_("particles.opposite_corner_textures_tile")),
    "TexOffset": SInfo(NUMBER, N_("particles.meshs_random_texture_offset")),
    "ValueMin": SInfo(NUMBER, N_("particles.curves_lowest_value")),
    "ValueMax": SInfo(NUMBER, N_("particles.curves_highest_value")),
    "ValueDef": SInfo(NUMBER, N_("particles.value_when_curve_no_points")),
    "Varianz": SInfo(NUMBER, N_("particles.how_much_particle_varies_factor")),
    "Size": SInfo(FIXED, N_("particles.curves_point_count_graph_edits")),
}  # fmt: skip

CURVES_2070 = {
    "PEP": N_("particles.milliseconds_between_two_particles"),
    "EmitArea": N_("particles.radius_ring_particles_born"),
    "EmitterRotX": N_("particles.emitters_turn_over_duration"),
    "EmitterRotY": N_("particles.emitters_turn_over_duration"),
    "EmitterRotZ": N_("particles.emitters_turn_over_duration"),
    "VelocityX": N_("particles.how_far_particle_moved_where"),
    "VelocityY": N_("particles.how_far_particle_moved_where"),
    "VelocityZ": N_("particles.how_far_particle_moved_where"),
    "Scale": N_("particles.half_particles_size_over_life"),
    "Rotation": N_("particles.particles_angle_over_life"),
    "Color": N_("particles.colour_over_particles_life"),
    "Alpha": N_("particles.opacity_over_particles_life"),
}

BRACE = {
    "Name": SInfo(TEXT, N_("particles.emitters_name")),
    "StartDelay": SInfo(NUMBER, N_("particles.milliseconds_after_effect_starts"), (), SINCE_ZERO),
    "Duration": SInfo(NUMBER, N_("particles.milliseconds_one_pass_lasts"), (), SINCE_ZERO),
    "Pause": SInfo(NUMBER, N_("particles.milliseconds_between_passes"), (), SINCE_ZERO),
    "Loop": SInfo(SWITCH, N_("particles.pass_repeats")),
    "StateBlock": SInfo(TEXT, N_("particles.render_state_engines_block_file")),
    "SrcBlend": SInfo(TEXT, N_("particles.blend_source_without_d3dblend_empty")),
    "DestBlend": SInfo(TEXT, N_("particles.blend_target_one_adds")),
    "Position": SInfo(TEXT, N_("particles.emitters_place_x_y_z")),
    "Rotation": SInfo(TEXT, N_("particles.emitters_turn_quaternion_x_y")),
    "ScaleFactors": SInfo(TEXT, N_("particles.emitters_scale_per_axis")),
    "Texture": SInfo(FILE, N_("particles.the_texture")),
    "Texture000": SInfo(FILE, N_("particles.the_texture")),
    "Rect": SInfo(TEXT, N_("particles.part_texture_shown_left_bottom")),
    "Alignment": SInfo(WHOLE, N_("particles.0_faces_camera")),
    "TimeScale": SInfo(NUMBER, N_("particles.pace_1_own"), (), (0.001, math.inf)),
    "Lighting": SInfo(SWITCH, N_("particles.lit")),
    "SoundID": SInfo(WHOLE, N_("particles.sound_started_emitter_0_none")),
    "Minimum": SInfo(NUMBER, N_("particles.graphs_lowest_value")),
    "Maximum": SInfo(NUMBER, N_("particles.graphs_highest_value")),
    "Single": SInfo(SWITCH, N_("particles.graph_value_throughout_off_points")),
    "Value": SInfo(NUMBER, N_("particles.value_when_graph_single")),
    "Linear": SInfo(SWITCH, N_("particles.straight_lines_between_points_off")),
    "Emit": SInfo(NUMBER, N_("particles.particles_second")),
    "LifeTime": SInfo(NUMBER, N_("particles.milliseconds_particle_lives"), (), SINCE_ZERO),
    "Speed": SInfo(NUMBER, N_("particles.units_second")),
    "PPS": SInfo(NUMBER, N_("particles.puffs_second")),
    "MinSize": SInfo(NUMBER, N_("particles.puffs_size_when_starts")),
    "MaxSize": SInfo(NUMBER, N_("particles.puffs_size_when_ends")),
    "Velocity": SInfo(NUMBER, N_("particles.units_second")),
}  # fmt: skip

TABLES = {"rdp": RDP, "xml": XML, "brace": BRACE}


def info(syntax: str, name: str) -> SInfo:
    """The field ``name`` of a file of ``syntax`` (a vector's part, ``Center.x``, as its vector)."""
    base = name.split(".", 1)[0]
    return TABLES.get(syntax, {}).get(base) or SInfo()


def curve_meaning(name: str) -> str:
    """What a 2070 or 1404 curve is ("" when not described); ``RotationX`` is ``Rotation``'s."""
    return CURVES_2070.get(name) or CURVES_2070.get(name.rstrip("XYZ"), "")
