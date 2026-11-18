"""GLSL 330 sources of the PC application's 3D view (``desktoprender``), ported from the page's three.js
shaders: the ground blends (the editor's weights, 117's bake, 2070's chunk layers), the water model,
banner leaves, the water shader's parts, the sky and the three frame finishes (rendering/README).

Every scene shader writes linear light into a half-float frame; ``FINAL_FS`` grades it. The scene is
mirrored on x (view x = size - x), so Anno's tiles are ``vec2(size - p.x, p.z)``.
"""

LIGHT = """
uniform vec3 sunDir; uniform vec3 sunColor;
uniform int ambientMode; uniform vec3 hemiSky; uniform vec3 hemiGround; uniform float envLight;
uniform vec3 sh[9]; uniform float shFactor; uniform vec3 flatAmbient;
uniform vec3 fogColor; uniform float fogNear; uniform float fogFar; uniform vec3 eye;
uniform vec3 uwLight; uniform vec2 uwFx;  // the underwater look: its light (zero: off), caustics on, time
uniform bool shadowOn; uniform sampler2DShadow shadowMap; uniform mat4 shadowMatrix;
const float PI = 3.14159265;
vec3 ambientAt(vec3 n) {
    if (ambientMode == 1) {  // the game's sky light, nine coefficients (three.js's shGetIrradianceAt)
        vec3 r = sh[0] * 0.886227 + sh[1] * 1.023328 * n.y + sh[2] * 1.023328 * n.z + sh[3] * 1.023328 * n.x
            + sh[4] * 0.858086 * n.x * n.y + sh[5] * 0.858086 * n.y * n.z + sh[6] * (0.743125 * n.z * n.z - 0.247708)
            + sh[7] * 0.858086 * n.x * n.z + sh[8] * 0.429043 * (n.x * n.x - n.y * n.y);
        return max(r, 0.0) * shFactor / PI;
    }
    if (ambientMode == 2) return flatAmbient;
    return mix(hemiGround, hemiSky, 0.5 + 0.5 * n.y) * 0.6 / PI + vec3(envLight);
}
float shadowAt(vec3 world, vec3 n) {
    if (!shadowOn) return 1.0;
    vec4 s = shadowMatrix * vec4(world + n * 0.04, 1.0);
    vec3 p = s.xyz / s.w * 0.5 + 0.5;
    if (p.x < 0.0 || p.x > 1.0 || p.y < 0.0 || p.y > 1.0 || p.z > 1.0) return 1.0;
    float sum = 0.0;
    vec2 texel = 1.0 / vec2(textureSize(shadowMap, 0));
    for (int i = -1; i <= 1; i++) for (int j = -1; j <= 1; j++)
        sum += texture(shadowMap, vec3(p.xy + vec2(i, j) * texel, p.z - 0.0008));
    return sum / 9.0;
}
vec3 lit(vec3 albedo, vec3 n, vec3 world, float shade) {
    float ndl = max(dot(n, sunDir), 0.0);
    return albedo * (ambientAt(n) + sunColor * ndl * shade);
}
const vec3 UW_EXP = vec3(1.97, 1.09, 1.09);
// two layers of light patterns drifting against each other, deeper = stronger (an approximation of a texture)
float causticLayer(vec2 p) {
    float a = sin(p.x + 1.7 * sin(p.y * 0.8)), b = sin(p.y * 1.1 + 1.7 * sin(p.x * 0.9));
    return pow(1.0 - abs(a * b), 6.0);
}
vec3 underwater(vec3 c, vec3 world) {
    vec3 toEye = eye - world;
    float dist = length(toEye);
    vec3 g = pow(max(c, 0.0), vec3(1.0 / 2.2));  // the formulas work on gamma colours
    float depth = max(clamp(-world.y / 36.0, 0.0, 1.0), clamp(dist / 72.0, 0.0, 1.0));
    if (uwFx.x > 0.5) {
        vec2 p = world.xz - world.y * 0.1;
        float k = causticLayer(p * 1.4 + uwFx.y * vec2(0.5, 0.3)) * causticLayer(p * 0.9 - uwFx.y * vec2(0.4, 0.5));
        g += vec3(20.0, 20.0, 16.0) * k * 0.12 * depth;
    }
    vec3 absorb = pow(vec3(1.0 - depth), UW_EXP);
    vec3 v = toEye / max(dist, 1e-4);
    // an eye above the sea counts as at the surface
    vec3 scatter = clamp(pow(vec3(clamp(1.0 - (v.y * 0.5 + 0.5), 0.0, 1.0)), UW_EXP) + min(eye.y, 0.0) / 72.0, 0.0, 1.0) * 0.6;
    float haze = pow(clamp(dist / 57.2, 0.0, 1.0), 2.52);
    return pow(max(mix(g * absorb, uwLight * scatter, haze), 0.0), vec3(2.2));
}
vec3 fogged(vec3 c, vec3 world) {
    if (uwLight.x > 0.0) return underwater(c, world);
    float d = length(world - eye);
    return mix(c, fogColor, smoothstep(fogNear, fogFar, d));
}
"""

# a normal map without tangents: the frame from the derivatives of position and uv (cheap, per pixel)
PERTURB = """
vec3 perturb(vec3 n, vec3 p, vec2 uv, vec3 m) {
    vec3 dp1 = dFdx(p), dp2 = dFdy(p);
    vec2 du1 = dFdx(uv), du2 = dFdy(uv);
    vec3 dp2perp = cross(dp2, n), dp1perp = cross(n, dp1);
    vec3 t = dp2perp * du1.x + dp1perp * du2.x;
    vec3 b = dp2perp * du1.y + dp1perp * du2.y;
    float inv = inversesqrt(max(max(dot(t, t), dot(b, b)), 1e-12));
    return normalize(mat3(t * inv, b * inv, n) * m);
}
"""

TERRAIN_VS = """
#version 330
uniform mat4 viewProj;
in vec3 pos; in vec3 normal;
out vec3 vWorld; out vec3 vNormal;
void main() { vWorld = pos; vNormal = normal; gl_Position = viewProj * vec4(pos, 1.0); }
"""

TERRAIN_FS = (
    """
#version 330
uniform float islandSize;
uniform int groundMode;  // 0 the baked image, 1 the editor's weights, 3 2070's layers
uniform int bakeOut;  // 1: the ground's albedo (linear), 2: its detail normal, drawn from above (a bake)
uniform bool hasAlbedo; uniform sampler2D albedo;
uniform sampler2DArray weights; uniform sampler2DArray diffuse; uniform sampler2DArray normals;
uniform int layers; uniform float repeat_[32]; uniform int rank[32]; uniform float sharp[32];
uniform sampler2DArray alpha2070; uniform usampler2D slots2070; uniform float side2070; uniform float per2070;
in vec3 vWorld; in vec3 vNormal; out vec4 color;
vec3 L(vec3 c) { return pow(c, vec3(2.2)); }  // the arrays hold the textures' sRGB values
"""
    + LIGHT
    + """
void main() {
    vec3 n = normalize(vNormal);
    vec2 g = vec2(islandSize - vWorld.x, vWorld.z);
    vec2 gdx = dFdx(g), gdy = dFdy(g);
    vec3 base = hasAlbedo ? texture(albedo, g / islandSize).rgb
                          : mix(vec3(0.3, 0.25, 0.18), vec3(0.16, 0.25, 0.09), clamp(vWorld.y / 8.0, 0.0, 1.0));
    vec3 gn = vec3(0.0, 0.0, 1.0);
    if (groundMode == 3) {  // 2070: each chunk its layers in its own order, raw gamma values (rendering-2070 2.2)
        vec2 chunk = floor(g / per2070), loc = fract(g / per2070);
        vec2 covUV = (chunk * 17.0 + 0.5 + loc * 16.0) / (17.0 * side2070);
        vec3 c = vec3(0.05), m = vec3(0.0, 0.0, 1.0);
        for (int k = 0; k < 24; k++) {
            uint sl = texelFetch(slots2070, ivec2(int(chunk.x) * 24 + k, int(chunk.y)), 0).r;
            if (sl > 254u) break;
            int s = int(sl);
            float rep = repeat_[s];
            vec3 uv = vec3(g / rep, float(s));
            vec4 d = textureGrad(diffuse, uv, gdx / rep, gdy / rep);
            float a = k == 0 ? 1.0 : 5.0 * (d.a - 1.0) + 6.0 * texture(alpha2070, vec3(covUV, float(k))).r;
            if (a <= 1.0 / 255.0) continue;
            a = clamp(a, 0.0, 1.0);
            c = mix(c, d.rgb, a);  // in gamma values, as 2070 blends
            m = mix(m, textureGrad(normals, uv, gdx / rep, gdy / rep).xyz * 2.0 - 1.0, a);
        }
        base = pow(c, vec3(2.2));
        gn = normalize(m);
    } else if (groundMode == 1) {  // the editor's weights, linear (what the paint brush changes)
        vec3 c = vec3(0.0), m = vec3(0.0);
        float sum = 0.0;
        for (int k = 0; k < 32; k++) {
            if (k >= layers) break;
            float w = texture(weights, vec3(g / islandSize, float(k))).r;
            if (w < 0.004) continue;
            float rep = repeat_[k];
            vec3 uv = vec3(g / rep, float(k));
            c += w * L(textureGrad(diffuse, uv, gdx / rep, gdy / rep).rgb);
            m += w * (textureGrad(normals, uv, gdx / rep, gdy / rep).xyz * 2.0 - 1.0);
            sum += w;
        }
        if (sum > 0.0) {
            float cover = clamp(sum * 4.0, 0.0, 1.0);
            base = mix(base, c / sum, cover);
            gn = normalize(mix(vec3(0.0, 0.0, 1.0), m / sum, cover));
        }
    }
    if (bakeOut == 1) { color = vec4(base, 1.0); return; }
    if (bakeOut == 2) { color = vec4(vec3(-gn.x, gn.y, gn.z) * 0.5 + 0.5, 1.0); return; }  // the mirror, tangent -x
    vec3 t = normalize(vec3(-1.0, 0.0, 0.0) - n * dot(n, vec3(-1.0, 0.0, 0.0)));  // +x in Anno's tiles
    vec3 b = cross(n, t);
    vec3 nn = normalize(t * gn.x + b * gn.y + n * gn.z);
    vec3 c = lit(base, nn, vWorld, shadowAt(vWorld, n));
    color = vec4(fogged(c, vWorld), 1.0);
}
"""
)

MODEL_VS = """
#version 330
uniform mat4 viewProj; uniform mat4 part; uniform vec2 uvOffset;
in vec3 pos; in vec3 normal; in vec2 uv; in mat4 inst;
out vec3 vWorld; out vec3 vNormal; out vec2 vUv;
void main() {
    mat4 model = inst * part;
    vec4 w = model * vec4(pos, 1.0);
    vWorld = w.xyz; vNormal = mat3(model) * normal; vUv = uv + uvOffset;
    gl_Position = viewProj * w;
}
"""

MODEL_FS = (
    """
#version 330
uniform sampler2D diffuseTex; uniform bool textured; uniform vec3 tint;
uniform sampler2D normalTex; uniform bool hasNormal; uniform float emissive;
uniform int kind;  // 0 opaque (cut-out), 1 the water shader's parts (see-through), 2 banner leaves, 3 decals
uniform float opacity; uniform vec4 flip;  // flipbook: cell x, cell y, grid x, grid y (grid 0: none)
uniform vec2 flipTiling;
in vec3 vWorld; in vec3 vNormal; in vec2 vUv; out vec4 color;
"""
    + LIGHT
    + PERTURB
    + """
void main() {
    vec2 uv = flip.z > 0.0 ? (fract(vUv * flipTiling) + flip.xy) / flip.zw : vUv;
    vec4 base = textured ? texture(diffuseTex, uv) : vec4(0.35, 0.35, 0.35, 1.0);
    if (kind == 3) {  // a decal: blended by its alpha and opacity, in the sun's shadow like the ground under it
        vec3 d = base.rgb * tint;
        color = vec4(fogged(lit(d, normalize(vNormal), vWorld, shadowAt(vWorld, vNormal)), vWorld), base.a * opacity);
        return;
    }
    if (kind != 1 && base.a < 0.35) discard;  // cut-out leaves and fences
    vec3 n = normalize(vNormal);
    if (dot(n, eye - vWorld) < 0.0) n = -n;  // double-sided (the mirror turns every winding round)
    if (hasNormal) {
        vec3 m = texture(normalTex, vUv).xyz * 2.0 - 1.0;
        m.y = -m.y;  // Anno's normal maps are DirectX-style (green down)
        n = perturb(n, vWorld, vUv, normalize(m));
    }
    if (kind == 2) n = normalize(n * 0.7 + normalize(eye - vWorld) * 0.3);  // the crown's outside, a little to the eye
    vec3 albedo = base.rgb * tint;
    vec3 c = lit(albedo, n, vWorld, kind == 1 ? 1.0 : shadowAt(vWorld, n)) + albedo * emissive;
    if (kind == 1) c += vec3(0.04);
    color = vec4(fogged(c, vWorld), kind == 1 ? base.a * opacity : 1.0);
}
"""
)

# banner leaves (shader117): every card's corners sit at its centre and are spread along the camera's
# axes, as the game does; the shadow pass spreads them along the sun's
BANNER_VS = """
#version 330
uniform mat4 viewProj; uniform mat4 view; uniform mat4 part;
in vec3 pos; in vec3 normal; in vec2 uv; in vec2 corner; in mat4 inst;
out vec3 vWorld; out vec3 vNormal; out vec2 vUv;
void main() {
    mat4 model = inst * part;
    vec4 w = model * vec4(pos, 1.0);
    mat3 toWorld = transpose(mat3(view));
    w.xyz += toWorld * vec3(corner * length(model[0].xyz), 0.0);
    vWorld = w.xyz; vNormal = mat3(model) * normal; vUv = uv;
    gl_Position = viewProj * w;
}
"""

DEPTH_FS = """
#version 330
uniform sampler2D diffuseTex; uniform bool textured;
in vec2 vUv; out vec4 color;
void main() {
    if (textured && texture(diffuseTex, vUv).a < 0.35) discard;
    color = vec4(1.0);
}
"""

PICK_FS = """
#version 330
uniform sampler2D diffuseTex; uniform bool textured; uniform int base;
in vec2 vUv; flat in int vId; out vec4 color;
void main() {  // the instance's number in 24 bits (an 8-bit target reads back on every driver)
    if (textured && texture(diffuseTex, vUv).a < 0.35) discard;
    int id = base + vId;
    color = vec4(float(id & 255), float((id >> 8) & 255), float((id >> 16) & 255), 255.0) / 255.0;
}
"""


def pick_vs(source: str) -> str:
    """A model vertex shader that also hands on its instance number (the pick pass)."""
    return source.replace("out vec2 vUv;", "out vec2 vUv; flat out int vId;").replace(
        "vUv = uv", "vId = gl_InstanceID + 1; vUv = uv", 1
    )


FLAT_VS = """
#version 330
uniform mat4 viewProj; uniform float pointSize;
in vec3 pos; in vec3 rgb;
out vec3 vRgb;
void main() { vRgb = rgb; gl_PointSize = pointSize; gl_Position = viewProj * vec4(pos, 1.0); }
"""
FLAT_FS = """
#version 330
uniform float alpha; uniform float gain;
in vec3 vRgb; out vec4 color;
void main() { color = vec4(vRgb * gain, alpha); }
"""

SKY_VS = """
#version 330
out vec2 vNdc;
void main() {
    vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2) * 2.0 - 1.0;
    vNdc = p; gl_Position = vec4(p, 1.0, 1.0);
}
"""
SKY_FS = """
#version 330
uniform mat4 invViewProj; uniform int skyMode; uniform vec3 zenith; uniform vec3 horizon; uniform float fallOff;
uniform vec3 below;
uniform vec3 uwLight; uniform vec3 eye;
in vec2 vNdc; out vec4 color;
void main() {
    vec4 a = invViewProj * vec4(vNdc, -1.0, 1.0), b = invViewProj * vec4(vNdc, 1.0, 1.0);
    vec3 d = normalize(b.xyz / b.w - a.xyz / a.w);
    float el = asin(clamp(d.y, -1.0, 1.0)) / 1.5707963;  // 1 zenith, 0 horizon
    vec3 c;
    if (uwLight.x > 0.0) {  // under water: the haze of an empty pixel, by the direction it is seen in
        const vec3 e = vec3(1.97, 1.09, 1.09);
        c = pow(clamp(pow(vec3(clamp(1.0 - (0.5 - d.y * 0.5), 0.0, 1.0)), e) + min(eye.y, 0.0) / 72.0, 0.0, 1.0) * 0.6 * uwLight, vec3(2.2));
    } else if (skyMode == 1) c = el <= 0.0 ? horizon : zenith + (horizon - zenith) * pow(1.0 - el, 3.0);  // the game's HDR sky
    else if (skyMode == 2) c = el <= 0.0 ? horizon : zenith + (horizon - zenith) * pow(1.0 - el, fallOff);  // 2070's
    else {  // the editor's: the page's gradient, its three colours from the theme (zenith, horizon, below)
        float v = 0.5 - el * 0.5;
        c = v < 0.47 ? mix(zenith, horizon, v / 0.47) : v < 0.5 ? mix(horizon, below, (v - 0.47) / 0.03) : below;
    }
    color = vec4(c, 1.0);
}
"""

# the editor's own water: see-through plain surfaces, lit
WATER_PLAIN_FS = (
    """
#version 330
uniform vec3 waterColor; uniform float opacity;
in vec3 vWorld; out vec4 color;
"""
    + LIGHT
    + """
void main() {
    vec3 n = vec3(0.0, 1.0, 0.0), v = normalize(eye - vWorld);
    vec3 h = normalize(v + sunDir);
    float spec = pow(max(dot(n, h), 0.0), 120.0) * 2.0;
    vec3 c = lit(waterColor, n, vWorld, shadowAt(vWorld, n)) + sunColor * spec * 0.2;
    float fres = pow(1.0 - max(dot(n, v), 0.0), 4.0);
    c = mix(c, fogColor, envLight * (0.3 + 1.6 * fres));  // the sky's reflection (the page's environment map)
    color = vec4(fogged(c, vWorld), mix(opacity, 1.0, fres * 0.5));
}
"""
)
WATER_VS = """
#version 330
uniform mat4 viewProj;
in vec3 pos;
out vec3 vWorld;
void main() { vWorld = pos; gl_Position = viewProj * vec4(pos, 1.0); }
"""

FINAL_VS = SKY_VS
FINAL_FS = """
#version 330
uniform sampler2D frame; uniform int finish;  // 0 the editor's (three.js ACES), 1 the game's grade, 2 2070's, 3 sRGB
uniform sampler3D lut; uniform bool hasLut; uniform float exposure; uniform vec4 mood;
in vec2 vNdc; out vec4 color;
// a post chain may leave a negative value in the frame: pow of it is NaN, a black pixel
vec3 srgb(vec3 c) { c = max(c, 0.0); return mix(12.92 * c, 1.055 * pow(c, vec3(1.0 / 2.4)) - 0.055, step(0.0031308, c)); }
vec3 rrt(vec3 v) { vec3 a = v * (v + 0.0245786) - 0.000090537; vec3 b = v * (0.983729 * v + 0.4329510) + 0.238081; return a / b; }
vec3 acesThree(vec3 c) {
    const mat3 inM = mat3(vec3(0.59719, 0.07600, 0.02840), vec3(0.35458, 0.90834, 0.13383), vec3(0.04823, 0.01566, 0.83777));
    const mat3 outM = mat3(vec3(1.60475, -0.10208, -0.00327), vec3(-0.53108, 1.10813, -0.07276), vec3(-0.07367, -0.00605, 1.07602));
    c *= exposure / 0.6;
    return clamp(outM * rrt(inM * c), 0.0, 1.0);
}
vec3 pq(vec3 x) { vec3 p = pow(max(x, 0.0), vec3(0.1593017578125));
    return pow((0.8359375 + 18.8515625 * p) / (1.0 + 18.6875 * p), vec3(78.84375)); }
const mat3 TO2020 = mat3(0.6274040, 0.0690970, 0.0163916, 0.3292820, 0.9195400, 0.0880132, 0.0433136, 0.0113612, 0.8955950);
float lows(float c) { return c < 0.1 ? 0.1 * pow(c / 0.1, 0.7) : c; }
vec3 acesGame(vec3 c) { vec3 v = 0.6 * c; return clamp(v * (2.51 * v + 0.03) / (v * (2.43 * v + 0.59) + 0.14), 0.0, 1.0); }
vec3 F(vec3 x) { const float A = 0.22, B = 0.30, C = 0.10, D = 0.20, E = 0.01, G = 0.30;
    return (x * (A * x + C * B) + D * E) / (x * (A * x + B) + D * G) - E / G; }
void main() {
    vec3 c = texture(frame, vNdc * 0.5 + 0.5).rgb;
    if (finish == 1) {  // rendering/lighting.md 7: the grade cube, exposure, the shadow lift, the game's ACES
        vec3 l = pq(TO2020 * c * 0.02) * (31.0 / 32.0) + 0.5 / 32.0;
        c = max((hasLut ? texture(lut, l).rgb : c) * exposure, 0.0);  // no cube read yet: ungraded
        c = acesGame(vec3(lows(c.r), lows(c.g), lows(c.b)));
    } else if (finish == 2) {  // rendering-2070.md 5: clipped at 2, Uncharted 2 white at 2, the mood colour
        vec3 x = min(c, 2.0);
        c = F(x) / F(vec3(2.0));
        c = clamp(mix(c, pow(mood.rgb, vec3(2.2)), dot(c, vec3(1.0 / 3.0)) * mood.a), 0.0, 1.0);
    } else if (finish != 3 && finish != 4) c = acesThree(c);  // 3: graded and tone-mapped by the game's shaders (gamepost)
    color = vec4(finish == 4 ? c : srgb(c), 1.0);  // 4: encoded too (2205's chain, postforward)
}
"""

OVERLAY_VS = """
#version 330
uniform mat4 viewProj;
in vec3 pos; in vec4 rgba;
out vec4 vRgba;
uniform float pointSize;
void main() { vRgba = rgba; gl_PointSize = pointSize; gl_Position = viewProj * vec4(pos, 1.0); }
"""
OVERLAY_FS = """
#version 330
in vec4 vRgba; out vec4 color;
void main() { color = vRgba; }
"""
