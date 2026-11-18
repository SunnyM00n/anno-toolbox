// The layer's checks that need no GPU: the names Python passes, and on Windows' software rasteriser (WARP) the
// resources, the patched descriptors and a frame of clears and copies read back. Exit 0 when all pass.
#include "gpu.h"

#include <cstdio>
#include <cstring>

using namespace gpu;

static int failed = 0;

static void check(bool ok, const char* what) {
    std::printf("%s %s\n", ok ? "ok  " : "FAIL", what);
    if (!ok) failed++;
}

static void names() {
    check(format_named("RGBA8_UNORM") == nvrhi::Format::RGBA8_UNORM, "a format by its name");
    check(format_name(format_named("BC7_UNORM_SRGB")) == "BC7_UNORM_SRGB", "a format's name back");
    check(format_named("") == nvrhi::Format::UNKNOWN, "no format");
    bool threw = false;
    try {
        format_named("NOT_A_FORMAT");
    } catch (const Error&) {
        threw = true;
    }
    check(threw, "an unknown format refused");
    check(int(compare_named("greater_equal")) == int(D3D12_COMPARISON_FUNC_GREATER_EQUAL),
          "comparisons have Direct3D's values");
    check(kind_named("structured") == nvrhi::ResourceType::StructuredBuffer_SRV, "a binding kind by its name");
    check(stage_named("cs") == nvrhi::ShaderType::Compute, "a stage by its name");
}

static void warp() {
    Device* d = nullptr;
    try {
        d = Device::create(false, Device::WARP);
    } catch (const Error& e) {
        std::printf("skip WARP: %s\n", e.what());
        return;
    }
    check(d->shader_model() >= int(D3D_SHADER_MODEL_6_2), "WARP takes shader model 6.2");
    Texture* t = d->texture(8, 4, "RGBA8_UNORM", 1, 1, "2d", "target", "t");
    Sampler* s = d->sampler("linear", "clamp", "greater_equal", 1.0f, 0.0f);
    check(s->patched && s->exact.ComparisonFunc == D3D12_COMPARISON_FUNC_GREATER_EQUAL, "a reversed comparison");
    Layout* l = d->layout("all", 0, {{"texture", 0, 1}, {"texture", 3, 1}, {"sampler", 11, 1}});
    Set* set = d->binding_set(*l, {Binding{"texture", 0, t}, Binding{"texture", 3}, Binding{"sampler", 11, nullptr,
                                                                                           nullptr, s}});
    check(set->get() != nullptr, "a set with a null texture and a patched sampler");
    Staging* st = d->staging(8, 4, "RGBA8_UNORM");
    CommandList c(*d);
    c.open();
    c.clear_color(*t, 1.0f, 0.5f, 0.0f, 1.0f);
    c.copy_to_staging(*st, *t);
    c.execute();
    c.close();
    std::string px = read_staging(*d, *st);
    check(px.size() == 8 * 4 * 4 && uint8_t(px[0]) == 255 && uint8_t(px[1]) >= 127 && uint8_t(px[1]) <= 128,
          "a clear read back");
    for (Held* h : std::initializer_list<Held*>{set, l, s, t, st}) delete h;
    d->wait_idle();
    auto lines = d->messages();
    check(d->log.errors() == 0, "no errors from NVRHI");
    for (const auto& m : lines) std::printf("     %s\n", m.c_str());
    delete d;
}

int main() {
    names();
    warp();
    std::printf(failed ? "%d failed\n" : "all passed\n", failed);
    return failed ? 1 : 0;
}
