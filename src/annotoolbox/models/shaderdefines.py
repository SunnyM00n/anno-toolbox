"""What Anno 117's engine hands its shader compiler besides the effect file: the define lists its subsystems build
when they load an effect (particles, border lines, build grid, SMAA, shadows, SSAO, ...), the files the game never
compiles, and the compiler's arguments (docs/rendering/shader-compile.md, "Defines from the engine", "The compile").

``compile_args`` is one compile's argument list; ``engine_lists(stem)`` the define lists the engine loads a file
with (the shipped cache records the same, ``CGameShaders.shipped_of``; the real-install test holds the table against
it); ``NOT_COMPILED`` the files only the studio's tools build, with why they cannot compile as shipped.
"""

from __future__ import annotations

from .shadercache import Switches, parse_switch_text

# the options of every compile, in the game's order (shader-compile.md, "The compile")
GAME_OPTIONS = (
    "-HV", "2021", "-all-resources-bound", "-pack-prefix-stable", "-Wno-for-redefinition", "-O3", "-Zi",
    "-Qstrip_debug", "-Zsb",
)  # fmt: skip
HALF_TYPES = "-enable-16bit-types"
HALF_ENTRIES = ("csConvertVerticesIndexed", "csConvertVertices")  # 16-bit types at their stage's model
SM66_ENTRIES = ("csDebugRT", "csRTGIInline")  # compute shaders compiled as cs_6_6 with 16-bit types


def compile_args(entry: str, profile: str) -> list[str]:
    """The compiler arguments of one pass shader as the game builds them: the entry, the options, 16-bit types for
    ray-tracing libraries and the entries named above, the profile (``cs_6_6`` for ``SM66_ENTRIES``), ``-Zpc``."""
    arguments = ["-E", entry, *GAME_OPTIONS]
    if profile.startswith("lib") or entry in HALF_ENTRIES:
        arguments.append(HALF_TYPES)
    if entry in SM66_ENTRIES:
        profile = "cs_6_6"
        arguments.append(HALF_TYPES)
    return [*arguments, "-T", profile, "-Zpc"]


def _fsr3() -> str:
    """AMD FSR 3's upscaler options and its bound resources, each bind switch 1."""
    options = (
        "UPSAMPLE_SAMPLERS_USE_DATA_HALF=0;ACCUMULATE_SAMPLERS_USE_DATA_HALF=0;REPROJECT_SAMPLERS_USE_DATA_HALF=1;"
    )
    options += (
        "POSTPROCESSLOCKSTATUS_SAMPLERS_USE_DATA_HALF=0;UPSAMPLE_USE_LANCZOS_TYPE=2;REPROJECT_USE_LANCZOS_TYPE=1;"
    )
    options += "HDR_COLOR_INPUT=1;LOW_RESOLUTION_MOTION_VECTORS=1;INVERTED_DEPTH=1;APPLY_SHARPENING=1"
    binds = (
        "CB_FSR3UPSCALER CB_RCAS CB_SPD SRV_SPD_MIPS SRV_INPUT_DEPTH SRV_REACTIVE_MASK SRV_INPUT_COLOR "
        "SRV_INPUT_MOTION_VECTORS SRV_INTERNAL_UPSCALED SRV_LUMA_HISTORY SRV_RCAS_INPUT SRV_ACCUMULATION "
        "SRV_SHADING_CHANGE SRV_FARTHEST_DEPTH SRV_FARTHEST_DEPTH_MIP1 SRV_CURRENT_LUMA SRV_LUMA_INSTABILITY "
        "SRV_PREVIOUS_LUMA SRV_NEW_LOCKS SRV_RECONSTRUCTED_PREV_NEAREST_DEPTH SRV_DILATED_MOTION_VECTORS "
        "SRV_DILATED_DEPTH SRV_LANCZOS_LUT SRV_DILATED_REACTIVE_MASKS SRV_FRAME_INFO SRV_INPUT_EXPOSURE "
        "SRV_TRANSPARENCY_AND_COMPOSITION_MASK UAV_LUMA_HISTORY UAV_INTERNAL_UPSCALED UAV_UPSCALED_OUTPUT "
        "UAV_ACCUMULATION UAV_SHADING_CHANGE UAV_FARTHEST_DEPTH UAV_FARTHEST_DEPTH_MIP1 UAV_CURRENT_LUMA "
        "UAV_LUMA_INSTABILITY UAV_NEW_LOCKS UAV_RECONSTRUCTED_PREV_NEAREST_DEPTH UAV_DILATED_DEPTH "
        "UAV_DILATED_MOTION_VECTORS UAV_DILATED_REACTIVE_MASKS UAV_FRAME_INFO UAV_SPD_MIPS_LEVEL_0 "
        "UAV_SPD_MIPS_LEVEL_1 UAV_SPD_MIPS_LEVEL_2 UAV_SPD_MIPS_LEVEL_3 UAV_SPD_MIPS_LEVEL_4 UAV_SPD_MIPS_LEVEL_5 "
        "UAV_SPD_GLOBAL_ATOMIC"
    ).split()
    head = "FFX_GPU=1;FFX_HLSL=1;" + ";".join(f"FFX_FSR3UPSCALER_OPTION_{option}" for option in options.split(";"))
    return head + ";" + ";".join(f"FSR3UPSCALER_BIND_{bind}=1" for bind in binds)


# the terrain shaders: the baked texture size, the tessellation quality setting and the switches it allows
_TERRAIN = (
    "TERRAIN_BAKED_TEX_SIZE=512;TESSELLATION_QUALITY=0;DIFFUSE_ENABLED=;NORMAL_ENABLED=;TINT_MAP_ENABLED=;"
    "GRIT_ENABLED=;MATERIAL_DISPLACEMENT_MAPPING_ENABLED=",
    "TERRAIN_BAKED_TEX_SIZE=512;TESSELLATION_QUALITY=1;DIFFUSE_ENABLED=;NORMAL_ENABLED=;TINT_MAP_ENABLED=;"
    "GRIT_ENABLED=;CAVITY_MAP_ENBLED=;MATERIAL_DISPLACEMENT_MAPPING_ENABLED=",
    "TERRAIN_BAKED_TEX_SIZE=512;TESSELLATION_QUALITY=2;DIFFUSE_ENABLED=;BUMP_ENABLED=;NORMAL_ENABLED=;"
    "PARALLAX_ENABLED=;SELF_SHADOWING_ENABLED=;TINT_MAP_ENABLED=;GRIT_ENABLED=;CAVITY_MAP_ENBLED=;"
    "MATERIAL_DISPLACEMENT_MAPPING_ENABLED=",
)
# shader file -> the define lists the engine loads it with, as the cache key writes them (update 3.0). Fixed numbers
# are the subsystems' own constants; several lists are settings (SSAO and ray-traced light quality, tessellation)
# or a second effect of the same file (area lights, the Egypt inland water, the five UI mask modes). Not here: the
# per-draw sets of debug.fx and ghost.fx and gpu_particles.fx's code from the particle data (shader-compile.md).
ENGINE_DEFINES: dict[str, tuple[str, ...]] = {
    "ambient_ssao": (
        "SSAO_THREAD_GROUPS_X=16;SSAO_THREAD_GROUPS_Y=16;NUM_DIRECTIONS=6;NUM_STEPS=2;KERNEL_RADIUS=2;"
        "SSAO_USE_DOWNSAMPLING=1;SSAO_DOWNSAMPLE_FACTOR=2",
        "SSAO_THREAD_GROUPS_X=16;SSAO_THREAD_GROUPS_Y=16;NUM_DIRECTIONS=8;NUM_STEPS=4;KERNEL_RADIUS=4;"
        "USE_ADAPTIVE_SAMPLING=1;SSAO_DOWNSAMPLE_FACTOR=1",
        "SSAO_THREAD_GROUPS_X=16;SSAO_THREAD_GROUPS_Y=16;NUM_DIRECTIONS=8;NUM_STEPS=4;KERNEL_RADIUS=4;"
        "USE_ADAPTIVE_SAMPLING=1;SSAO_USE_DOWNSAMPLING=1;SSAO_DOWNSAMPLE_FACTOR=2",
    ),
    "borderline": ("MAX_INTERSECTING_CIRCLES=24;NUM_INTERSECTION_AREA_CONSTANTS=24;CONSTANTS_PER_INTERSECTION_AREA=2",),
    "deferred_shading": (
        "DEFERRED_SHADING_TILE_SIZE_X=16;DEFERRED_SHADING_TILE_SIZE_Y=16",
        "DEFERRED_SHADING_TILE_SIZE_X=16;DEFERRED_SHADING_TILE_SIZE_Y=16;ENABLE_AREA_LIGHT=",
    ),
    "distance_cluster": ("DCLUSTER_ATLAS_WIDTH=2048;DCLUSTER_ATLAS_HEIGHT=2048;DCLUSTER_ATLAS_PARTITION_SIZE=32",),
    "distance_cluster_atlas_packer": (
        "ATLAS_PACKER_THREAD_GROUPS_X=8;ATLAS_PACKER_THREAD_GROUPS_Y=8;DCLUSTER_ATLAS_PARTITION_SIZE=32",
    ),
    "environment": (
        "SNOW_CELL_SIZE=10.000000;SNOW_CELL_EXTENDS=30.000000;RAIN_CELL_SIZE=8.000000;"
        "RAIN_CELL_EXTENDS=30.000000;FOG_MAP_RESOLUTION=0.250000;DOWNSAMPLE_FACTOR=2;FOG_MIP_DOWNSCALE=4;"
        "CLOUD_LAYER_LOWERBOUND=190;CLOUD_LAYER_UPPERBOUND=370;SSC_CACHEDIMS_WS=2550.000000;"
        "VOLUME_SAMPLE_SHADOWMAP=",
    ),
    "fsr3upscaler": (_fsr3(),),
    "light": (
        "NUM_LIGHT_CONSTANTS=256;CONSTANTS_PER_POINT_SPRITE=3;CONSTANTS_PER_LENSFLARE=3;"
        "CONSTANTS_PER_SPOT_LIGHT=10;CONSTANTS_PER_QUAD_LIGHT=3;CONSTANTS_PER_PROJECTOR_LIGHT=7",
    ),
    "local_update_mask_blur": ("LOCAL_UPDATE_MASK_NUM_CHUNK_CONSTANTS=256;CHUNK_SIZE=16",),
    "local_water_area": ("MAX_LOCAL_WATER_AREA_COUNT=32;LOCAL_WATER_AREA_TEXEL_SIZE_WS=64",),
    "misc": (
        "NUM_GRID_HIGHLIGHT_CONSTANTS=5;NUM_WIRELINE_VERTEX_CONSTANTS=256;CONSTANTS_PER_BUILD_GRID=2;"
        "MAX_BUILD_GRID_CONSTANTS=128;CONSTANTS_PER_BUILD_GRID_STYLE=3;NUM_BUILD_GRID_STYLE_CONSTANTS=48;"
        "TERRAIN_LINE_SUBDIVISION_LENGTH=1.000000",
    ),
    "model_culler": ("MODEL_CULLER_THREAD_GROUPS_X=512",),
    "model_data_updater": ("MODEL_DATA_UPDATER_THREAD_GROUPS_X=64",),
    "ocean_simulation": ("FFT_WRAPUP_THREAD_GROUPS=32;FFT_GRADIENTS_THREAD_GROUPS=16;FFT_MAX_MAP_SIZE=256",),
    "particle_bindless": (
        "PARTICLE_MAX_MATERIAL_COUNT=8;PARTICLE_MAX_FRAME_COUNT=256;PARTICLE_MAX_FRAME_COUNT_BY_128=2;"
        "PARTICLE_MAX_SORTED_PARTICLE_COUNT_BY_4=512;PARTICLE_LINE_SEGMENT_COUNT=16",
    ),
    "phoenix_gui": tuple(
        f"{mode}=;ENABLE_SLUG=;MAX_NUM_PHOENIX_SLUG_TEXTURES=8"
        for mode in ("PHOENIX_CONTINUE_MASK", "PHOENIX_INCREMENT_MASK", "PHOENIX_MASK", "PHOENIX_MASKED")
        + ("PHOENIX_UNMASKED",)
    ),
    "posteffect_depth_of_field": ("RT_SIZE=1",),
    "prop_generate": (
        "PROP_VEGETATE_THREAD_GROUPS_X=16;PROP_VEGETATE_THREAD_GROUPS_Y=16;PROP_GENERATE_THREAD_GROUPS_X=8;"
        "PROP_GENERATE_THREAD_GROUPS_Y=8",
    ),
    "prop_visibility": ("PROP_VISIBILITY_THREAD_GROUPS_X=8;PROP_VISIBILITY_THREAD_GROUPS_Y=8",),
    "raytrace_gi": (
        "DENOISE_DIVISOR=1;TRACE_DIVISOR=2;RAYS_PER_PIXEL=1;RTGI_QUALITY=2;RTGI_WRITE_VELOCITY=1",
        "DENOISE_DIVISOR=1;TRACE_DIVISOR=2;RAYS_PER_PIXEL=1;RTGI_QUALITY=3",
        "DENOISE_DIVISOR=1;TRACE_DIVISOR=2;RAYS_PER_PIXEL=1;RTGI_QUALITY=3;RTGI_WRITE_VELOCITY=1",
        "DENOISE_DIVISOR=1;TRACE_DIVISOR=3;RAYS_PER_PIXEL=1;RTGI_QUALITY=1;RTGI_WRITE_VELOCITY=1",
        "DENOISE_DIVISOR=2;TRACE_DIVISOR=2;RAYS_PER_PIXEL=1;RTGI_QUALITY=1;RTGI_WRITE_VELOCITY=1",
        "DENOISE_DIVISOR=2;TRACE_DIVISOR=2;RAYS_PER_PIXEL=1;RTGI_QUALITY=2;RTGI_WRITE_VELOCITY=1",
    ),
    "raytrace_mesh_build": ("HITBOX_CELL_SIZE=16",),
    "raytrace_reflections": ("RAYS_PER_PIXEL=1",),
    "sb_clear": ("MAX_WORKGROUP_DIM_X=65535",),
    "screen_space_shadows": ("SSS_THREAD_GROUPS_X=16;SSS_THREAD_GROUPS_Y=16",),
    "shadow": (
        "Z_REDUCTION_THREADGROUP_DIM=8;Z_REDUCTION_THREADGROUP_SIZE=64;Z_REDUCTION_TILE_SIZE=64;"
        "SHADOW_MAP_CASCADE_COUNT=4;SDSM_LIGHTSPACE_ARRAY_SIZE=256",
    ),
    "sky": ("CLOUD_LAYER_LOWERBOUND=190;CLOUD_LAYER_UPPERBOUND=370;SSC_CACHEDIMS_WS=2550.000000",),
    "smaa": (
        "MAX_LIVESEARCHDIST=8;MAX_BAKEDSEARCHDIST=8;NUM_BAKEDPATTERNS=4;NUM_BAKEDEDGETYPES=4;TAAPATTERN_MSAA2X=;"
        "NUM_BAKEDSUBSAMPLES=2;NAN_CHECK=",
    ),
    "spline_data_updater": ("SPLINE_DATA_UPDATER_THREAD_GROUPS_X=64",),
    "terrain": _TERRAIN,
    "terrain_britannia": _TERRAIN,
    "terrain_compress": (
        "TERRAIN_COMPRESS_THREAD_GROUPS_X=8;TERRAIN_COMPRESS_THREAD_GROUPS_Y=8;HEIGHT_COMPRESS_DELTA_COUNT=3",
    ),
    "terrain_dawn": _TERRAIN,
    "undiscovered": ("NUM_UNDISCOVERED_NODE_CONSTANTS=512",),
    "undiscovered_compute": (
        "UNDISCOVERED_BLURRED_SCALE=8;UNDISCOVERED_BLURRED_RADIUS=1.500000;UNDISCOVERED_BLURRED_KERNEL_SIZE=5;"
        "NUM_UNDISCOVERED_BLUR_CHUNK_CONSTANTS=512;UNDISCOVERED_BLUR_WORKGROUPSIZE=32;"
        "NUM_UNDISCOVERED_FADE_CHUNK_CONSTANTS=512;UNDISCOVERED_CHUNK_SIZE=8",
    ),
    "water_cluster_flow": ("MAX_RIVER_FLOW_SPEED=8.000000",),
    "water_effects": ("SPRAY_GRID_CELL_SIZE=64;NUM_WATER_EFFECTS_UPDATE_THREADS=8;NUM_PARTICLES_PER_CELL=8",),
    "water_inland_water": ("", "EGYPT_INLAND_WATER="),
    "water_ocean": ("", "PHYSICAL_WAVES_ENABLED="),
    "water_physical_waves": ("PHYSWAVES_THREAD_GROUPS_X=8;PHYSWAVES_THREAD_GROUPS_Y=8",),
}
# files the game ships but never compiles (no precompiled file is required of them and none ships), whose pass
# shaders cannot compile as shipped: what is missing
NOT_COMPILED = {
    "model_vat3": "psModelVATForwardPreZ reads input.InstanceID, which PSInModel does not have (a source error)",
    "posteffect_kuwahara_filter": "its constants exist only under ENABLE_PHOTOMODE (with it, it compiles)",
    "posteffect_photomode": "its constants exist only under ENABLE_PHOTOMODE (with it, it compiles)",
    "prototype_gpu_performance": "TRIANGLE_SCREEN_SIZE is defined nowhere",
    "raytrace_ao_denoise": "RTAO_DENOISE_THREAD_GROUPS_X/Y and RAYTRACED_RES_DIVISOR are defined nowhere",
}


def engine_lists(stem: str) -> list[Switches]:
    """The define lists the engine loads ``stem`` with (empty for a file it loads without, or per material)."""
    return [parse_switch_text(text) for text in ENGINE_DEFINES.get(stem, ())]
