"""Our plain look inside the game's-shaders look: what the view draws itself there (its ground plane, the groups the
game's shaders do not draw: still compiling, failed or of a kind they leave) lit in the frame's own light, with no
imitation of a game's materials (viewer.md "Looks").

``LIT_FS`` is the fragment shader (with the island editor's ``MODEL_VS``, or ``SKIN_VS`` for a skin posed on the GPU):
the colour and normal maps, the sun with its shadow, the sky's ambient and plain reflection, the config's point lights
by a plain falloff, the haze. ``light_uniforms(lights, places, color_of, middle)`` packs the lights,
``decal_quad(extents)`` makes a decal's rectangle.
"""

from __future__ import annotations

import numpy as np

from ..render import shaders as S

MAX_LIGHTS = 16  # point lights drawn at once (the nearest to the model's middle win)
GLOSS = 0.35  # one gloss for every surface: no material's own

DECAL_LIFT = 0.004  # above the ground plane, against z-fighting

# a skin posed on the GPU: per vertex four joints and weights, the joints' skin matrices in a float texture (one
# row a joint, one texel a column), the rest as MODEL_VS
SKIN_VS = """
#version 330
uniform mat4 viewProj; uniform mat4 part; uniform vec2 uvOffset; uniform sampler2D jointTex;
in vec3 pos; in vec3 normal; in vec2 uv; in vec4 joints; in vec4 weights; in mat4 inst;
out vec3 vWorld; out vec3 vNormal; out vec2 vUv;
mat4 joint(float j) {
    int k = min(int(j + 0.5), textureSize(jointTex, 0).y - 1);
    return mat4(texelFetch(jointTex, ivec2(0, k), 0), texelFetch(jointTex, ivec2(1, k), 0),
                texelFetch(jointTex, ivec2(2, k), 0), texelFetch(jointTex, ivec2(3, k), 0));
}
void main() {
    mat4 skin = weights.x * joint(joints.x) + weights.y * joint(joints.y) + weights.z * joint(joints.z)
        + weights.w * joint(joints.w);
    mat4 model = inst * part;
    vec4 w = model * (skin * vec4(pos, 1.0));
    vWorld = w.xyz; vNormal = mat3(model) * (mat3(skin) * normal); vUv = uv + uvOffset;
    gl_Position = viewProj * w;
}
"""

LIT_FS = (
    f"""
#version 330
uniform sampler2D diffuseTex; uniform bool textured; uniform vec3 tint;
uniform sampler2D normalTex; uniform bool hasNormal;
uniform float opacity; uniform float dayLight;
uniform int kind;  // 0 opaque (cut below the alpha reference), 3 blended by alpha and opacity (decals)
uniform int lightCount; uniform vec3 lightPos[{MAX_LIGHTS}]; uniform vec3 lightColor[{MAX_LIGHTS}];
uniform vec2 lightReach[{MAX_LIGHTS}];
in vec3 vWorld; in vec3 vNormal; in vec2 vUv; out vec4 color;
"""
    + S.LIGHT
    + S.PERTURB
    + f"const float GLOSS = {GLOSS};\n"
    + """
const vec3 F0 = vec3(0.04);  // every surface a dielectric
vec3 brdf(vec3 n, vec3 v, vec3 l, vec3 e, vec3 albedo, float rough) {
    float nl = dot(n, l);
    if (nl <= 0.0) return vec3(0.0);
    vec3 h = normalize(v + l);
    float nh = max(dot(n, h), 1e-4), nv = max(dot(n, v), 1e-4), vh = max(dot(v, h), 1e-4);
    nl = max(nl, 1e-4);
    float a = max(rough, 0.01); a *= a;
    float a2 = a * a, d = nh * nh * (a2 - 1.0) + 1.0;
    float D = a2 / (PI * d * d);
    float k = (rough + 1.0) * (rough + 1.0) / 8.0;
    float vis = 1.0 / ((nl * (1.0 - k) + k) * (nv * (1.0 - k) + k));
    vec3 F = F0 + (1.0 - F0) * exp2((-5.55473 * vh - 6.98316) * vh);
    return e * (albedo * nl + F * (PI / 4.0) * D * vis * nl);
}
vec2 envBrdf(float rough, float nv) {  // the split sum's table, fitted (Karis)
    vec4 c0 = vec4(-1.0, -0.0275, -0.572, 0.022), c1 = vec4(1.0, 0.0425, 1.04, -0.04);
    vec4 r = rough * c0 + c1;
    float a004 = min(r.x * r.x, exp2(-9.28 * nv)) * r.x + r.y;
    return vec2(-1.04, 1.04) * a004 + r.zw;
}
void main() {
    vec4 base = textured ? texture(diffuseTex, vUv) : vec4(0.35, 0.35, 0.35, 1.0);
    if (kind == 0 && base.a < 0.35) discard;
    vec3 n = normalize(vNormal);
    vec3 v = normalize(eye - vWorld);
    if (dot(n, v) < 0.0) n = -n;
    if (hasNormal) {  // RG the normal (DirectX, green down), z rebuilt
        vec2 xy = texture(normalTex, vUv).xy * 2.0 - 1.0;
        n = perturb(n, vWorld, vUv, normalize(vec3(xy.x, -xy.y, sqrt(max(1.0 - dot(xy, xy), 0.0)))));
    }
    float rough = 1.0 - GLOSS;
    vec3 albedo = base.rgb * tint;
    vec3 c = brdf(n, v, sunDir, sunColor * shadowAt(vWorld, n) * dayLight, albedo, rough);
    for (int i = 0; i < lightCount; i++) {
        vec3 l = lightPos[i] - vWorld;
        float d = length(l);
        float att = pow(clamp(1.0 - d / max(lightReach[i].x, 1e-3), 0.0, 1.0), max(lightReach[i].y, 0.01));
        c += brdf(n, v, l / max(d, 1e-4), lightColor[i] * att, albedo, rough);
    }
    float nv = max(dot(n, v), 1e-4);
    c += ambientAt(n) * dayLight * albedo;
    vec3 fr = F0 + (max(vec3(1.0 - rough), F0) - F0) * pow(1.0 - nv, 5.0);
    vec2 dfg = envBrdf(rough, nv);
    c += ambientAt(reflect(-v, n)) * PI * dayLight * (fr * dfg.x + dfg.y);  // the sky's plain reflection
    color = vec4(fogged(c, vWorld), kind == 3 ? base.a * opacity : 1.0);
}
"""
)


def light_uniforms(lights, places, color_of, middle) -> tuple[int, bytes, bytes, bytes]:
    """(count, positions, colours, (reach, falloff)) of the lights nearest ``middle`` (view space), packed as the
    shader's arrays; ``places`` their matrices in view space, ``color_of(light)`` its colour this frame."""
    found = sorted(
        (
            (float(np.linalg.norm(matrix[:3, 3] - middle)), light, matrix)
            for light, matrix in zip(lights, places, strict=True)
        ),
        key=lambda row: row[0],
    )[:MAX_LIGHTS]
    positions = np.zeros((MAX_LIGHTS, 3), "f4")
    col = np.zeros((MAX_LIGHTS, 3), "f4")
    reach = np.zeros((MAX_LIGHTS, 2), "f4")
    for k, (_d, light, matrix) in enumerate(found):
        positions[k], col[k], reach[k] = matrix[:3, 3], color_of(light), (light.range, light.falloff)
    return len(found), positions.tobytes(), col.tobytes(), reach.tobytes()


def decal_quad(extents: tuple[float, float]) -> tuple[np.ndarray, np.ndarray]:
    """A decal's rectangle in its own space: (vertices [4, 8] position, normal up, uv; indices [6])."""
    x, z = extents
    verts = np.array([[-x, DECAL_LIFT, -z, 0, 1, 0, 0, 0], [x, DECAL_LIFT, -z, 0, 1, 0, 1, 0],
                      [x, DECAL_LIFT, z, 0, 1, 0, 1, 1], [-x, DECAL_LIFT, z, 0, 1, 0, 0, 1]], "f4")  # fmt: skip
    return verts, np.array([0, 1, 2, 0, 2, 3], "u4")
