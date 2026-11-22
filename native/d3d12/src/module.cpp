// The Python face of the layer (nanobind): one class per resource, the device's factories, command lists and the
// recording into a foreign list. The Python side is render/nativegpu.py; the names it takes are NVRHI's.
#include <nanobind/nanobind.h>
#include <nanobind/stl/optional.h>
#include <nanobind/stl/pair.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/tuple.h>
#include <nanobind/stl/vector.h>

#include "gpu.h"

namespace nb = nanobind;
using namespace nb::literals;
using namespace gpu;

#ifndef MODULE_VERSION
#define MODULE_VERSION "0"
#endif
#ifndef NVRHI_REVISION
#define NVRHI_REVISION "unknown"
#endif

static std::string bytes_of(const nb::bytes& b) { return std::string(b.c_str(), b.size()); }

template <class T>
static T* as(nb::handle h) {
    return h.is_none() ? nullptr : nb::cast<T*>(h);
}

// (kind, slot, resource) or (kind, slot, resource, {"size": .., "element": .., "format": .., "mip": .., ...})
static Binding binding_of(nb::handle item) {
    nb::tuple t = nb::cast<nb::tuple>(item);
    Binding b;
    b.kind = nb::cast<std::string>(t[0]);
    b.slot = nb::cast<uint32_t>(t[1]);
    nb::handle r = t.size() > 2 ? t[2] : nb::none();
    if (!r.is_none()) {
        if (nb::isinstance<Texture>(r))
            b.texture = nb::cast<Texture*>(r);
        else if (nb::isinstance<Buffer>(r))
            b.buffer = nb::cast<Buffer*>(r);
        else if (nb::isinstance<Sampler>(r))
            b.sampler = nb::cast<Sampler*>(r);
        else
            throw Error("a binding's resource is a Texture, a Buffer, a Sampler or None");
    }
    if (t.size() > 3) {
        nb::dict o = nb::cast<nb::dict>(t[3]);
        if (o.contains("size")) b.size = nb::cast<uint32_t>(o["size"]);
        if (o.contains("element")) b.element = nb::cast<uint32_t>(o["element"]);
        if (o.contains("format")) b.format = nb::cast<std::string>(o["format"]);
        if (o.contains("mip")) b.mip = nb::cast<uint32_t>(o["mip"]);
        if (o.contains("mips")) b.mips = nb::cast<uint32_t>(o["mips"]);
        if (o.contains("dimension")) b.dimension = nb::cast<std::string>(o["dimension"]);
    }
    if ((b.kind == "sampler" && !b.sampler)) throw Error("a sampler binding without its sampler");
    return b;
}

static std::vector<nvrhi::IBindingSet*> bindings_of(const nb::list& sets) {
    std::vector<nvrhi::IBindingSet*> out;
    for (nb::handle h : sets) {
        if (nb::isinstance<Set>(h))
            out.push_back(nb::cast<Set*>(h)->get());
        else if (nb::isinstance<Table>(h))
            out.push_back(nb::cast<Table*>(h)->get());
        else
            throw Error("a draw's bindings are Sets and Tables");
        if (!out.back()) throw Error("a released set or table in a draw's bindings");
    }
    return out;
}

static Draw draw_of(Pipeline& pipeline, const nb::list& bindings, uint32_t count, uint32_t instances, uint32_t first,
                    uint32_t base_vertex, uint32_t first_instance, const nb::list& vertex_buffers,
                    nb::handle index_buffer, bool index32, uint64_t index_offset, const nb::bytes& push) {
    Draw d;
    d.pipeline = &pipeline;
    d.bindings = bindings_of(bindings);
    for (nb::handle h : vertex_buffers) {
        if (nb::isinstance<Buffer>(h)) {
            d.vertex_buffers.emplace_back(nb::cast<Buffer*>(h), 0);
        } else {
            nb::tuple t = nb::cast<nb::tuple>(h);
            d.vertex_buffers.emplace_back(nb::cast<Buffer*>(t[0]), nb::cast<uint64_t>(t[1]));
        }
    }
    d.index_buffer = as<Buffer>(index_buffer);
    d.index32 = index32;
    d.index_offset = index_offset;
    d.count = count;
    d.instances = instances;
    d.first = first;
    d.base_vertex = base_vertex;
    d.first_instance = first_instance;
    d.push = bytes_of(push);
    return d;
}

static PipelineState state_of(const nb::dict& s) {
    PipelineState p;
    auto get = [&](const char* k) { return s.contains(k) ? nb::handle(s[k]) : nb::handle(); };
    if (auto v = get("topology")) p.topology = nb::cast<std::string>(v);
    if (auto v = get("cull")) p.cull = nb::cast<std::string>(v);
    if (auto v = get("front_ccw")) p.front_ccw = nb::cast<bool>(v);
    if (auto v = get("depth_test")) p.depth_test = nb::cast<bool>(v);
    if (auto v = get("depth_write")) p.depth_write = nb::cast<bool>(v);
    if (auto v = get("depth_func")) p.depth_func = nb::cast<std::string>(v);
    if (auto v = get("depth_clip")) p.depth_clip = nb::cast<bool>(v);
    if (auto v = get("depth_bias")) p.depth_bias = nb::cast<int>(v);
    if (auto v = get("slope_bias")) p.slope_bias = nb::cast<float>(v);
    if (auto v = get("bias_clamp")) p.bias_clamp = nb::cast<float>(v);
    if (auto v = get("stencil")) p.stencil = nb::cast<bool>(v);
    if (auto v = get("stencil_func")) p.stencil_func = nb::cast<std::string>(v);
    if (auto v = get("stencil_pass")) p.stencil_pass = nb::cast<std::string>(v);
    if (auto v = get("stencil_fail")) p.stencil_fail = nb::cast<std::string>(v);
    if (auto v = get("stencil_depth_fail")) p.stencil_depth_fail = nb::cast<std::string>(v);
    if (auto v = get("stencil_read_mask")) p.stencil_read_mask = nb::cast<int>(v);
    if (auto v = get("stencil_write_mask")) p.stencil_write_mask = nb::cast<int>(v);
    if (auto v = get("stencil_ref")) p.stencil_ref = nb::cast<int>(v);
    if (auto v = get("wireframe")) p.wireframe = nb::cast<bool>(v);
    if (auto v = get("colors")) p.colors = nb::cast<std::vector<std::string>>(v);
    if (auto v = get("depth")) p.depth = nb::cast<std::string>(v);
    if (auto v = get("samples")) p.samples = nb::cast<uint32_t>(v);
    if (auto v = get("blends")) p.blends = nb::cast<decltype(p.blends)>(v);
    return p;
}

NB_MODULE(_nativegpu, m) {
    m.doc() = "Direct3D 12 through NVRHI for the game look (docs/rendering/native-backend.md)";
    nb::set_leak_warnings(false);  // devices cached in Python live until the interpreter ends
    nb::register_exception_translator([](const std::exception_ptr& p, void*) {
        try {
            std::rethrow_exception(p);
        } catch (const Error& e) {
            PyErr_SetString(PyExc_RuntimeError, e.what());
        }
    });
    m.def("version", [] { return std::string(MODULE_VERSION); });
    m.def("nvrhi_revision", [] { return std::string(NVRHI_REVISION); });
    m.def("formats", [] {
        std::vector<std::string> out;
        for (int f = 1; f < int(nvrhi::Format::COUNT); f++) out.push_back(format_name(nvrhi::Format(f)));
        return out;
    });

    nb::class_<Buffer>(m, "Buffer")
        .def_ro("size", &Buffer::size)
        .def_prop_ro("gpu_address", &Buffer::gpu_address)
        .def_prop_ro("native", &Buffer::native, "its ID3D12Resource*")
        .def("release", &Buffer::release);
    nb::class_<Texture>(m, "Texture")
        .def_prop_ro("width", [](Texture& t) { return t.desc.width; })
        .def_prop_ro("height", [](Texture& t) { return t.desc.height; })
        .def_prop_ro("mips", [](Texture& t) { return t.desc.mipLevels; })
        .def_prop_ro("format", [](Texture& t) { return format_name(t.desc.format); })
        .def_prop_ro("native", &Texture::native, "its ID3D12Resource*")
        .def("release", &Texture::release);
    nb::class_<Sampler>(m, "Sampler").def("release", &Sampler::release);
    nb::class_<Layout>(m, "Layout").def_ro("bindless", &Layout::bindless).def("release", &Layout::release);
    nb::class_<Set>(m, "Set").def("release", &Set::release);
    nb::class_<Table>(m, "Table").def("release", &Table::release);
    nb::class_<Shader>(m, "Shader").def("release", &Shader::release);
    nb::class_<InputLayout>(m, "InputLayout").def("release", &InputLayout::release);
    nb::class_<Pipeline>(m, "Pipeline").def_ro("compute", &Pipeline::compute).def("release", &Pipeline::release);
    nb::class_<Framebuffer>(m, "Framebuffer")
        .def_ro("width", &Framebuffer::width)
        .def_ro("height", &Framebuffer::height)
        .def("release", &Framebuffer::release);
    nb::class_<Staging>(m, "Staging").def("release", &Staging::release);
    nb::class_<Timer>(m, "Timer").def("release", &Timer::release);
    // a draw made once and recorded every frame (``record_draws``): it keeps the pipeline and buffers it names alive
    // (``draw_item``'s keep_alive) and its sets' objects; one of them released raises when it is recorded
    nb::class_<Draw>(m, "DrawItem")
        .def_ro("count", &Draw::count)
        .def_prop_ro("indexed", [](Draw& d) { return d.index_buffer != nullptr; });

    nb::class_<Device>(m, "Device")
        .def_static("attach", &Device::attach, "device"_a, "queue"_a, nb::rv_policy::take_ownership,
                    "NVRHI on a device and queue someone else made (QRhi's: rhinative.handles)")
        .def_static("create", &Device::create, "debug"_a = false, "adapter"_a = -1, nb::rv_policy::take_ownership,
                    "a device of our own (the debug layer where Windows' Graphics Tools are installed)")
        .def_ro("owned", &Device::owned)
        .def_ro("debug", &Device::debug)
        .def_prop_ro("adapter", &Device::adapter_name)
        .def_prop_ro("shader_model", &Device::shader_model)
        .def_prop_ro("binding_tier", &Device::binding_tier)
        .def_prop_ro("video_memory", &Device::video_memory)
        .def_prop_ro("errors", [](Device& d) { return d.log.errors(); })
        .def_prop_ro("native", [](Device& d) { return uintptr_t(d.d3d.Get()); })
        .def_rw("keep_frames", &Device::keep_frames)
        .def("messages", &Device::messages)
        .def("end_frame", &Device::end_frame)
        .def("wait_idle", &Device::wait_idle, nb::call_guard<nb::gil_scoped_release>())
        .def("buffer", &Device::buffer, "size"_a, "usage"_a = "", "stride"_a = 0, "format"_a = "", "name"_a = "",
             nb::keep_alive<0, 1>(), nb::rv_policy::take_ownership)
        .def("texture", &Device::texture, "width"_a, "height"_a, "format"_a, "depth"_a = 1, "mips"_a = 1,
             "kind"_a = "2d", "usage"_a = "", "name"_a = "", nb::keep_alive<0, 1>(), nb::rv_policy::take_ownership)
        .def("sampler", &Device::sampler, "filter"_a = "linear", "address"_a = "clamp", "compare"_a = "",
             "anisotropy"_a = 16.0f, "mip_bias"_a = 0.0f, nb::keep_alive<0, 1>(), nb::rv_policy::take_ownership)
        .def(
            "layout",
            [](Device& d, const std::vector<std::tuple<std::string, uint32_t, uint32_t>>& items, uint32_t space,
               const std::string& visibility) {
                std::vector<LayoutItem> list;
                for (const auto& [k, s, c] : items) list.push_back({k, s, c});
                return d.layout(visibility, space, list);
            },
            "items"_a, "space"_a = 0, "visibility"_a = "all", nb::keep_alive<0, 1>(), nb::rv_policy::take_ownership)
        .def("bindless_layout",
             [](Device& d, const std::vector<std::pair<std::string, uint32_t>>& spaces, uint32_t first,
                uint32_t capacity, const std::string& visibility) {
                 return d.bindless_layout(visibility, first, capacity, spaces);
             },
             "spaces"_a, "first"_a = 0, "capacity"_a = 0, "visibility"_a = "all", nb::keep_alive<0, 1>(),
             nb::rv_policy::take_ownership)
        .def(
            "binding_set",
            [](Device& d, Layout& layout, const nb::list& items) {
                std::vector<Binding> list;
                for (nb::handle h : items) list.push_back(binding_of(h));
                return d.binding_set(layout, list);
            },
            "layout"_a, "items"_a, nb::keep_alive<0, 1>(), nb::rv_policy::take_ownership)
        .def("descriptor_table", &Device::descriptor_table, "layout"_a, "size"_a, nb::keep_alive<0, 1>(),
             nb::rv_policy::take_ownership)
        .def(
            "write_table",
            [](Device& d, Table& t, nb::handle item) { d.write_table(t, binding_of(item)); }, "table"_a, "item"_a)
        .def(
            "shader",
            [](Device& d, const std::string& stage, const nb::bytes& code, const std::string& entry) {
                return d.shader(stage, bytes_of(code), entry);
            },
            "stage"_a, "bytecode"_a, "entry"_a = "main", nb::keep_alive<0, 1>(), nb::rv_policy::take_ownership)
        .def("input_layout", &Device::input_layout, "elements"_a, nb::keep_alive<0, 1>(),
             nb::rv_policy::take_ownership)
        .def(
            "graphics_pipeline",
            [](Device& d, nb::handle vs, nb::handle ps, nb::handle input, const std::vector<Layout*>& layouts,
               const nb::dict& state, nb::handle hs, nb::handle ds) {
                Shader* v = as<Shader>(vs);
                Shader* p = as<Shader>(ps);
                Shader* h = as<Shader>(hs);
                Shader* m = as<Shader>(ds);
                InputLayout* i = as<InputLayout>(input);
                PipelineState s = state_of(state);
                nb::gil_scoped_release unlocked;  // the driver may compile for a while
                return graphics_pipeline(d, v, h, m, p, i, layouts, s);
            },
            "vs"_a, "ps"_a.none(), "input_layout"_a.none(), "layouts"_a, "state"_a, "hs"_a = nb::none(), "ds"_a = nb::none(),
            nb::keep_alive<0, 1>(), nb::rv_policy::take_ownership)
        .def("wrap_texture", &Device::wrap_texture, "resource"_a, "format"_a, "kind"_a = "", "name"_a = "",
             nb::keep_alive<0, 1>(), nb::rv_policy::take_ownership,
             "a QRhi texture's resource (qrhi_texture) for binding sets; never in a command list of NVRHI's")
        .def("wrap_buffer", &Device::wrap_buffer, "resource"_a, "stride"_a = 0, "usage"_a = "", "name"_a = "",
             nb::keep_alive<0, 1>(), nb::rv_policy::take_ownership)
        .def(
            "compute_pipeline",
            [](Device& d, Shader& cs, const std::vector<Layout*>& layouts) { return compute_pipeline(d, cs, layouts); },
            "cs"_a, "layouts"_a, nb::keep_alive<0, 1>(), nb::rv_policy::take_ownership)
        .def(
            "framebuffer",
            [](Device& d, const std::vector<Texture*>& colors, nb::handle depth, uint32_t mip, uint32_t layer) {
                return d.framebuffer(colors, as<Texture>(depth), mip, layer);
            },
            "colors"_a, "depth"_a = nb::none(), "mip"_a = 0, "layer"_a = 0, nb::keep_alive<0, 1>(),
            nb::rv_policy::take_ownership)
        .def("staging", &Device::staging, "width"_a, "height"_a, "format"_a, nb::keep_alive<0, 1>(),
             nb::rv_policy::take_ownership)
        .def(
            "read",
            [](Device& d, Staging& s) {
                std::string out;
                {
                    nb::gil_scoped_release unlocked;
                    out = read_staging(d, s);
                }
                return nb::bytes(out.data(), out.size());
            },
            "staging"_a)
        .def("timer", &Device::timer, nb::keep_alive<0, 1>(), nb::rv_policy::take_ownership)
        .def("timer_ms", &Device::timer_ms, "timer"_a)
        .def(
            "command_list", [](Device& d) { return new CommandList(d); }, nb::keep_alive<0, 1>(),
            nb::rv_policy::take_ownership);

    nb::class_<CommandList>(m, "CommandList")
        .def("open", &CommandList::open)
        .def("close", &CommandList::close)
        .def("execute", &CommandList::execute, nb::call_guard<nb::gil_scoped_release>())
        .def_prop_ro(
            "native",
            [](CommandList& c) {
                return uintptr_t(c.get()->getNativeObject(nvrhi::ObjectTypes::D3D12_GraphicsCommandList).pointer);
            },
            "its ID3D12GraphicsCommandList* (the tests record a foreign list's checks into it)")
        .def(
            "write_buffer",
            [](CommandList& c, Buffer& b, const nb::bytes& data, uint64_t offset) {
                c.write_buffer(b, bytes_of(data), offset);
            },
            "buffer"_a, "data"_a, "offset"_a = 0)
        .def(
            "write_texture",
            [](CommandList& c, Texture& t, const nb::bytes& data, size_t row_pitch, uint32_t layer, uint32_t mip,
               size_t depth_pitch) { c.write_texture(t, layer, mip, bytes_of(data), row_pitch, depth_pitch); },
            "texture"_a, "data"_a, "row_pitch"_a, "layer"_a = 0, "mip"_a = 0, "depth_pitch"_a = 0)
        .def("clear_color", &CommandList::clear_color, "texture"_a, "r"_a, "g"_a, "b"_a, "a"_a)
        .def("clear_depth", &CommandList::clear_depth, "texture"_a, "depth"_a = 0.0f, "stencil"_a = 0)
        .def("clear_uint", &CommandList::clear_uint, "buffer"_a, "value"_a)
        .def("copy_to_staging", &CommandList::copy_to_staging, "staging"_a, "texture"_a)
        .def("copy_texture", &CommandList::copy_texture, "dst"_a, "src"_a)
        .def("copy_region", &CommandList::copy_region, "dst"_a, "dst_layer"_a, "dst_mip"_a, "x"_a, "y"_a, "src"_a,
             "src_layer"_a, "src_mip"_a, "width"_a, "height"_a)
        .def("transitions", &CommandList::transitions, "items"_a,
             "(resource, before, after) transitions ahead of NVRHI's own barriers on the open list")
        .def("uav_barrier", &CommandList::uav_barrier,
             "every unordered access before it done before any after it (NVRHI places none for an unchanged set)")
        .def(
            "draw",
            [](CommandList& c, Framebuffer& fb, Pipeline& p, const nb::list& bindings, uint32_t count,
               uint32_t instances, uint32_t first, uint32_t base_vertex, uint32_t first_instance,
               const nb::list& vertex_buffers, nb::handle index_buffer, bool index32, uint64_t index_offset,
               const nb::bytes& push, std::tuple<float, float, float, float> viewport) {
                Draw d = draw_of(p, bindings, count, instances, first, base_vertex, first_instance, vertex_buffers,
                                 index_buffer, index32, index_offset, push);
                auto [x, y, w, h] = viewport;
                c.draw(fb, d, !index_buffer.is_none(), x, y, w, h);
            },
            "framebuffer"_a, "pipeline"_a, "bindings"_a, "count"_a, "instances"_a = 1, "first"_a = 0,
            "base_vertex"_a = 0, "first_instance"_a = 0, "vertex_buffers"_a = nb::list(),
            "index_buffer"_a = nb::none(), "index32"_a = true, "index_offset"_a = 0, "push"_a = nb::bytes(""),
            "viewport"_a = std::make_tuple(0.0f, 0.0f, 0.0f, 0.0f))
        .def(
            "dispatch",
            [](CommandList& c, Pipeline& p, const nb::list& bindings, uint32_t x, uint32_t y, uint32_t z,
               const nb::bytes& push) {
                Draw d = draw_of(p, bindings, 0, 1, 0, 0, 0, nb::list(), nb::none(), true, 0, push);
                c.dispatch(d, x, y, z);
            },
            "pipeline"_a, "bindings"_a, "x"_a, "y"_a = 1, "z"_a = 1, "push"_a = nb::bytes(""))
        .def("begin_timer", &CommandList::begin_timer, "timer"_a)
        .def("end_timer", &CommandList::end_timer, "timer"_a)
        .def("marker", &CommandList::marker, "name"_a, "begin"_a = true)
        .def("set_state", &CommandList::set_state, "texture"_a, "state"_a);

    m.def("qrhi_texture", &qrhi_texture, "texture"_a,
          "(ID3D12Resource*, the state QRhi tracks it in) of a QRhiTexture* (shiboken's pointer)");
    m.def("qrhi_buffer", &qrhi_buffer, "buffer"_a, "slot"_a = 0, "a QRhiBuffer*'s ID3D12Resource* of a frame slot");
    m.def("qrhi_create_from", &qrhi_create_from, "texture"_a, "resource"_a, "state"_a,
          "QRhiTexture::createFrom of a QRhiTexture* not made yet: the module's resource, tracked from state");
    m.def("transitions", &transitions, "list"_a, "items"_a,
          "(resource, before, after) transitions recorded on a command list someone else owns");
    m.def(
        "record_external",
        [](Device& device, uintptr_t list, Pipeline& p, const nb::list& bindings, uint32_t count,
           std::tuple<float, float, float, float> viewport, const nb::list& vertex_buffers, nb::handle index_buffer,
           bool index32, uint32_t instances, uint32_t first, const nb::bytes& push) {
            Draw d = draw_of(p, bindings, count, instances, first, 0, 0, vertex_buffers, index_buffer, index32, 0, push);
            auto [x, y, w, h] = viewport;
            record_external(device, list, d, !index_buffer.is_none(), x, y, w, h);
        },
        "device"_a, "list"_a, "pipeline"_a, "bindings"_a, "count"_a, "viewport"_a, "vertex_buffers"_a = nb::list(),
        "index_buffer"_a = nb::none(), "index32"_a = true, "instances"_a = 1, "first"_a = 0, "push"_a = nb::bytes(""),
        "a draw recorded into a command list someone else owns (QRhi's, inside beginExternal), into its bound targets");
    m.def(
        "draw_item",
        [](Pipeline& p, const nb::list& bindings, uint32_t count, const nb::list& vertex_buffers,
           nb::handle index_buffer, bool index32, const nb::bytes& push) {
            auto* d = new Draw(draw_of(p, bindings, count, 1, 0, 0, 0, vertex_buffers, index_buffer, index32, 0, push));
            // its sets' objects held: a set let go while the draw is kept would leave it a freed pointer
            for (nb::handle h : bindings)
                d->held.push_back(nb::isinstance<Set>(h) ? nb::cast<Set*>(h)->handle : nb::cast<Table*>(h)->handle);
            return d;
        },
        "pipeline"_a, "bindings"_a, "count"_a, "vertex_buffers"_a = nb::list(), "index_buffer"_a = nb::none(),
        "index32"_a = true, "push"_a = nb::bytes(""), nb::keep_alive<0, 1>(), nb::keep_alive<0, 4>(),
        nb::keep_alive<0, 5>(), nb::rv_policy::take_ownership,
        "a draw made once for record_draws (indexed when it has an index buffer); it keeps its pipeline and buffers "
        "alive, and its sets' objects");
    m.def(
        "record_draws",
        [](Device& device, uintptr_t list, const std::vector<const Draw*>& draws,
           const std::vector<uint32_t>& instances, const std::vector<uint64_t>& offsets,
           std::tuple<float, float, float, float> viewport) {
            auto [x, y, w, h] = viewport;
            record_draws(device, list, draws, instances, offsets, x, y, w, h);
        },
        "device"_a, "list"_a, "draws"_a, "instances"_a, "offsets"_a, "viewport"_a,
        "DrawItems recorded into a foreign list, each with its instances and root offset (cbModel's slot) this frame");
    m.def(
        "record_dispatch",
        [](Device& device, uintptr_t list, Pipeline& p, const nb::list& bindings, std::tuple<uint32_t, uint32_t,
           uint32_t> groups) {
            Draw d = draw_of(p, bindings, 0, 1, 0, 0, 0, nb::list(), nb::none(), true, 0, nb::bytes(""));
            auto [x, y, z] = groups;
            record_dispatch(device, list, d, x, y, z);
        },
        "device"_a, "list"_a, "pipeline"_a, "bindings"_a, "groups"_a,
        "a compute dispatch recorded into a command list someone else owns (QRhi's, inside beginExternal)");
    m.def("uav_barrier", &uav_barrier, "list"_a, "a barrier between every unordered access before and after it");
    m.def("copy_buffer", &copy_buffer, "list"_a, "dst"_a, "dst_offset"_a, "src"_a, "src_offset"_a, "size"_a,
          "bytes between two buffers (ID3D12Resource*) on a foreign list, both in their copy states");
}
