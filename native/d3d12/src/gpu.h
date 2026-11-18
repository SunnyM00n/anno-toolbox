// The module's Direct3D 12 layer over NVRHI: a device (made here, or QRhi's taken over), its resources, binding
// layouts and pipelines from DXIL as the game ships it, command lists, and the draw recorded into a command list
// someone else owns. module.cpp binds it to Python; README.md says what each part is for.
#pragma once

#define NOMINMAX
#include <nvrhi/d3d12.h>
#include <nvrhi/nvrhi.h>
#include <nvrhi/utils.h>
#include <nvrhi/validation.h>

#include <directx/d3d12.h>
#include <dxgi1_6.h>
#include <wrl/client.h>

#include <cstdint>
#include <deque>
#include <mutex>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace gpu {

using Microsoft::WRL::ComPtr;

struct Error : std::runtime_error {
    using std::runtime_error::runtime_error;
};

// NVRHI's messages and the debug layer's, kept until Python reads them
class MessageLog : public nvrhi::IMessageCallback {
public:
    void message(nvrhi::MessageSeverity severity, const char* text) override;
    std::vector<std::string> take();
    int errors() const { return errors_; }

private:
    std::mutex lock_;
    std::vector<std::string> lines_;
    int errors_ = 0;
};

nvrhi::Format format_named(const std::string& name);  // NVRHI's names ("RGBA8_UNORM", "D32", "BC7_UNORM_SRGB")
std::string format_name(nvrhi::Format format);
nvrhi::ShaderType stage_named(const std::string& name);  // vs, ps, cs, all
nvrhi::ResourceType kind_named(const std::string& name);
nvrhi::ComparisonFunc compare_named(const std::string& name);
nvrhi::ResourceStates state_named(const std::string& name);

struct Device;

// A resource with the device that made it: released a few frames late (a command list of QRhi's may still read it)
struct Held {
    Device* device = nullptr;
    nvrhi::ResourceHandle handle;
    Held() = default;
    Held(Device* d, nvrhi::IResource* h) : device(d), handle(h) {}
    Held(const Held&) = default;
    Held& operator=(const Held&) = default;
    virtual ~Held();
    void release();
};

struct Buffer : Held {
    nvrhi::BufferHandle get() const { return static_cast<nvrhi::IBuffer*>(handle.Get()); }
    uint64_t size = 0;
    uint64_t gpu_address() const;
};

struct Texture : Held {
    nvrhi::ITexture* get() const { return static_cast<nvrhi::ITexture*>(handle.Get()); }
    nvrhi::TextureDesc desc;
};

struct Sampler : Held {
    nvrhi::ISampler* get() const { return static_cast<nvrhi::ISampler*>(handle.Get()); }
    D3D12_SAMPLER_DESC exact{};  // what the descriptor holds (NVRHI's comparison is LESS only: patched in)
    bool patched = false;
};

struct Layout : Held {
    nvrhi::IBindingLayout* get() const { return static_cast<nvrhi::IBindingLayout*>(handle.Get()); }
    bool bindless = false;
};

struct Set : Held {
    nvrhi::IBindingSet* get() const { return static_cast<nvrhi::IBindingSet*>(handle.Get()); }
};

struct Table : Held {
    nvrhi::IDescriptorTable* get() const { return static_cast<nvrhi::IDescriptorTable*>(handle.Get()); }
};

struct Shader : Held {
    nvrhi::IShader* get() const { return static_cast<nvrhi::IShader*>(handle.Get()); }
};

struct InputLayout : Held {
    nvrhi::IInputLayout* get() const { return static_cast<nvrhi::IInputLayout*>(handle.Get()); }
};

struct Pipeline : Held {
    bool compute = false;
    nvrhi::IGraphicsPipeline* graphics() const;
    nvrhi::IComputePipeline* compute_pipeline() const;
};

struct Framebuffer : Held {
    nvrhi::IFramebuffer* get() const { return static_cast<nvrhi::IFramebuffer*>(handle.Get()); }
    uint32_t width = 0, height = 0;
};

struct Staging : Held {
    nvrhi::IStagingTexture* get() const { return static_cast<nvrhi::IStagingTexture*>(handle.Get()); }
    nvrhi::TextureDesc desc;
};

struct Timer : Held {
    nvrhi::ITimerQuery* get() const { return static_cast<nvrhi::ITimerQuery*>(handle.Get()); }
};

// What a binding set item names: one of the resources above, or nothing (a null descriptor of the item's kind)
struct Binding {
    std::string kind;
    uint32_t slot = 0;
    Texture* texture = nullptr;
    Buffer* buffer = nullptr;
    Sampler* sampler = nullptr;
    uint32_t size = 0;            // push constants' bytes
    uint32_t element = 0;         // a bindless table's index
    std::string format;           // a typed view's format ("" the resource's own)
    uint32_t mip = 0, mips = 0;   // a texture view's levels (0 levels: all)
    std::string dimension;        // "2d", "2darray", "3d", "cube" ("" the texture's own)
};

struct LayoutItem {
    std::string kind;
    uint32_t slot = 0;
    uint32_t count = 1;  // an array of descriptors (push constants: their bytes)
};

struct Device {
    ComPtr<ID3D12Device> d3d;
    ComPtr<ID3D12CommandQueue> queue;
    ComPtr<ID3D12InfoQueue> info_queue;
    nvrhi::DeviceHandle nv;        // the validation layer over it when debug is on
    nvrhi::d3d12::IDevice* native = nullptr;  // NVRHI's own device beneath: its heaps
    MessageLog log;
    bool owned = false;   // made here (route B), not taken over from QRhi
    bool debug = false;   // the debug layer is on
    uint64_t frame = 0;   // advanced by end_frame: what the late releases count in
    std::deque<std::pair<uint64_t, nvrhi::ResourceHandle>> late;
    std::mutex late_lock;
    uint32_t keep_frames = 3;  // QRhi's frames in flight and one more [ours]

    static Device* attach(uintptr_t device, uintptr_t queue);
    static constexpr int WARP = -2;  // create's adapter: Windows' software rasteriser
    static Device* create(bool debug, int adapter);
    ~Device();

    void end_frame();        // one frame done: what was released three frames ago goes
    void wait_idle();
    void release_late(nvrhi::IResource* handle);
    std::vector<std::string> messages();  // NVRHI's and the debug layer's since the last call
    std::string adapter_name() const;
    int shader_model() const;   // 0x62 for 6.2 ...
    int binding_tier() const;

    Buffer* buffer(uint64_t size, const std::string& usage, uint32_t stride, const std::string& format,
                   const std::string& name);
    Texture* texture(uint32_t width, uint32_t height, const std::string& format, uint32_t depth, uint32_t mips,
                     const std::string& kind, const std::string& usage, const std::string& name);
    Sampler* sampler(const std::string& filter, const std::string& address, const std::string& compare,
                     float anisotropy, float mip_bias);
    Layout* layout(const std::string& visibility, uint32_t space, const std::vector<LayoutItem>& items);
    Layout* bindless_layout(const std::string& visibility, uint32_t first, uint32_t capacity,
                            const std::vector<std::pair<std::string, uint32_t>>& spaces);
    Set* binding_set(Layout& layout, const std::vector<Binding>& items);
    Table* descriptor_table(Layout& layout, uint32_t size);
    void write_table(Table& table, const Binding& item);
    Shader* shader(const std::string& stage, const std::string& bytecode, const std::string& entry);
    InputLayout* input_layout(const std::vector<std::tuple<std::string, uint32_t, std::string, uint32_t, uint32_t,
                                                           uint32_t, bool>>& elements);
    Framebuffer* framebuffer(const std::vector<Texture*>& colors, Texture* depth, uint32_t mip, uint32_t layer);
    Staging* staging(uint32_t width, uint32_t height, const std::string& format);
    Timer* timer();
    // QRhi's resources (interop.cpp) for binding sets only: NVRHI tracks no state of theirs, so they never go in a
    // command list of NVRHI's; a draw on QRhi's list moves them with ``transitions``
    Texture* wrap_texture(uintptr_t resource, const std::string& format, const std::string& kind,
                          const std::string& name);
    Buffer* wrap_buffer(uintptr_t resource, uint32_t stride, const std::string& usage, const std::string& name);
    float timer_ms(Timer& t);   // -1 until the query is done
};

// a graphics pipeline's fixed state, as the game's render state blocks give it (renderstates.PassState)
struct PipelineState {
    std::string topology = "triangles";
    std::string cull = "back";
    bool front_ccw = false;
    bool depth_test = true;
    bool depth_write = true;
    std::string depth_func = "greater_equal";  // 117's depth is reversed: 1 near
    bool depth_clip = true;
    int depth_bias = 0;
    float slope_bias = 0.0f;
    float bias_clamp = 0.0f;
    bool stencil = false;
    bool wireframe = false;
    // per colour target: enable, src, dst, op, src alpha, dst alpha, op alpha, write mask
    std::vector<std::tuple<bool, std::string, std::string, std::string, std::string, std::string, std::string, int>>
        blends;
    std::vector<std::string> colors;  // the targets' formats
    std::string depth;                 // the depth target's format ("" none)
    uint32_t samples = 1;
};

// hs and ds: the tessellated pipelines (topology "patch4", the patches' control points by the topology)
Pipeline* graphics_pipeline(Device& device, Shader* vs, Shader* hs, Shader* ds, Shader* ps, InputLayout* input,
                            const std::vector<Layout*>& layouts, const PipelineState& state);
Pipeline* compute_pipeline(Device& device, Shader& cs, const std::vector<Layout*>& layouts);

// One draw or dispatch's bindings and arguments, as the command list takes them
struct Draw {
    Pipeline* pipeline = nullptr;
    std::vector<nvrhi::IBindingSet*> bindings;  // sets and bindless tables, in the pipeline's layout order
    std::vector<std::pair<Buffer*, uint64_t>> vertex_buffers;
    Buffer* index_buffer = nullptr;
    bool index32 = true;
    uint64_t index_offset = 0;
    uint32_t count = 0, instances = 1, first = 0, base_vertex = 0, first_instance = 0;
    std::string push;  // push constants' bytes
};

class CommandList {
public:
    CommandList(Device& device);
    void open();
    void close();
    void execute();   // close, submit on the device's queue, open again
    void write_buffer(Buffer& b, const std::string& data, uint64_t offset);
    void write_texture(Texture& t, uint32_t layer, uint32_t mip, const std::string& data, size_t row_pitch,
                       size_t depth_pitch);
    void clear_color(Texture& t, float r, float g, float b, float a);
    void clear_depth(Texture& t, float depth, uint32_t stencil);
    void clear_uint(Buffer& b, uint32_t value);
    void copy_to_staging(Staging& s, Texture& t);
    void copy_texture(Texture& dst, Texture& src);
    void draw(Framebuffer& fb, const Draw& d, bool indexed, float x, float y, float w, float h);
    void dispatch(const Draw& d, uint32_t x, uint32_t y, uint32_t z);
    void begin_timer(Timer& t);
    void end_timer(Timer& t);
    void marker(const std::string& name, bool begin);
    void set_state(Texture& t, const std::string& state);
    nvrhi::ICommandList* get() { return list_.Get(); }

private:
    Device& device_;
    nvrhi::CommandListHandle list_;
    bool open_ = false;
};

std::string read_staging(Device& device, Staging& s);  // the pixels, rows packed (waits for the GPU)

// Recording into a command list someone else made (QRhi's, between beginExternal and endExternal): our heaps,
// root signature, pipeline, tables and the draw; the render targets stay those the list has bound
void record_external(Device& device, uintptr_t list, const Draw& d, bool indexed, float x, float y, float w, float h);

// A QRhiTexture's resource and the state QRhi tracks it in; a QRhiBuffer's resource of a frame slot (interop.cpp)
std::pair<uintptr_t, int> qrhi_texture(uintptr_t texture);
uintptr_t qrhi_buffer(uintptr_t buffer, int slot);
// transitions on a list: (resource, state before, state after), D3D12_RESOURCE_STATES
void transitions(uintptr_t list, const std::vector<std::tuple<uintptr_t, int, int>>& items);

}  // namespace gpu
