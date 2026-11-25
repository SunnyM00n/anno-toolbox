// The device: made here or taken over from QRhi, its messages, the late releases and the names Python uses.
#include "gpu.h"

#include <d3d12/d3d12-backend.h>  // NVRHI's own types: the sampler descriptors patched in (resources.cpp)

#include <algorithm>
#include <map>

#pragma comment(lib, "d3d12.lib")
#pragma comment(lib, "dxgi.lib")
#pragma comment(lib, "dxguid.lib")

namespace gpu {

void MessageLog::message(nvrhi::MessageSeverity severity, const char* text) {
    static const char* names[] = {"info", "warning", "error", "fatal"};
    std::lock_guard<std::mutex> hold(lock_);
    if (severity >= nvrhi::MessageSeverity::Error) errors_++;
    if (lines_.size() < 4096) lines_.push_back(std::string(names[int(severity)]) + ": " + text);
}

std::vector<std::string> MessageLog::take() {
    std::lock_guard<std::mutex> hold(lock_);
    std::vector<std::string> out;
    out.swap(lines_);
    return out;
}

nvrhi::Format format_named(const std::string& name) {
    static std::map<std::string, nvrhi::Format> names = [] {
        std::map<std::string, nvrhi::Format> m;
        for (int f = 0; f < int(nvrhi::Format::COUNT); f++)
            m[nvrhi::getFormatInfo(nvrhi::Format(f)).name] = nvrhi::Format(f);
        return m;
    }();
    if (name.empty()) return nvrhi::Format::UNKNOWN;
    auto it = names.find(name);
    if (it == names.end()) throw Error("unknown format " + name);
    return it->second;
}

std::string format_name(nvrhi::Format format) { return nvrhi::getFormatInfo(format).name; }

nvrhi::ShaderType stage_named(const std::string& name) {
    static const std::map<std::string, nvrhi::ShaderType> names = {
        {"vs", nvrhi::ShaderType::Vertex},  {"ps", nvrhi::ShaderType::Pixel},
        {"cs", nvrhi::ShaderType::Compute}, {"gs", nvrhi::ShaderType::Geometry},
        {"hs", nvrhi::ShaderType::Hull},    {"ds", nvrhi::ShaderType::Domain},
        {"all", nvrhi::ShaderType::All},    {"graphics", nvrhi::ShaderType::AllGraphics}};
    auto it = names.find(name);
    if (it == names.end()) throw Error("unknown stage " + name);
    return it->second;
}

nvrhi::ResourceType kind_named(const std::string& name) {
    using R = nvrhi::ResourceType;
    static const std::map<std::string, R> names = {
        {"texture", R::Texture_SRV},          {"rwtexture", R::Texture_UAV},
        {"typed", R::TypedBuffer_SRV},        {"rwtyped", R::TypedBuffer_UAV},
        {"structured", R::StructuredBuffer_SRV}, {"rwstructured", R::StructuredBuffer_UAV},
        {"raw", R::RawBuffer_SRV},            {"rwraw", R::RawBuffer_UAV},
        {"cbuffer", R::ConstantBuffer},       {"volatile", R::VolatileConstantBuffer},
        {"sampler", R::Sampler},              {"push", R::PushConstants}};
    auto it = names.find(name);
    if (it == names.end()) throw Error("unknown binding kind " + name);
    return it->second;
}

nvrhi::ComparisonFunc compare_named(const std::string& name) {
    using C = nvrhi::ComparisonFunc;
    static const std::map<std::string, C> names = {
        {"never", C::Never}, {"less", C::Less},       {"equal", C::Equal},          {"less_equal", C::LessOrEqual},
        {"greater", C::Greater}, {"not_equal", C::NotEqual}, {"greater_equal", C::GreaterOrEqual}, {"always", C::Always}};
    auto it = names.find(name);
    if (it == names.end()) throw Error("unknown comparison " + name);
    return it->second;
}

nvrhi::ResourceStates state_named(const std::string& name) {
    using S = nvrhi::ResourceStates;
    static const std::map<std::string, S> names = {
        {"common", S::Common},           {"shader_resource", S::ShaderResource}, {"render_target", S::RenderTarget},
        {"depth_write", S::DepthWrite},  {"depth_read", S::DepthRead},           {"uav", S::UnorderedAccess},
        {"copy_source", S::CopySource},  {"copy_dest", S::CopyDest},             {"vertex", S::VertexBuffer},
        {"index", S::IndexBuffer},       {"constant", S::ConstantBuffer},        {"present", S::Present}};
    auto it = names.find(name);
    if (it == names.end()) throw Error("unknown resource state " + name);
    return it->second;
}

Held::~Held() { release(); }

void Held::release() {
    if (handle && device) device->release_late(handle.Get());
    handle = nullptr;
}

uint64_t Buffer::gpu_address() const {
    if (!handle) throw Error("the buffer was released");
    auto* res = static_cast<ID3D12Resource*>(get()->getNativeObject(nvrhi::ObjectTypes::D3D12_Resource).pointer);
    return res ? res->GetGPUVirtualAddress() : 0;
}

nvrhi::IGraphicsPipeline* Pipeline::graphics() const {
    if (compute) throw Error("a compute pipeline where a graphics one was wanted");
    return static_cast<nvrhi::IGraphicsPipeline*>(handle.Get());
}

nvrhi::IComputePipeline* Pipeline::compute_pipeline() const {
    if (!compute) throw Error("a graphics pipeline where a compute one was wanted");
    return static_cast<nvrhi::IComputePipeline*>(handle.Get());
}

static void make_nvrhi(Device* d) {
    nvrhi::d3d12::DeviceDesc desc;
    desc.errorCB = &d->log;
    desc.pDevice = d->d3d.Get();
    desc.pGraphicsCommandQueue = d->queue.Get();
    desc.shaderResourceViewHeapSize = 1 << 18;  // the island's maps and the frame's tables [ours]
    desc.samplerHeapSize = 2048;
    desc.enableEnhancedBarriers = false;  // QRhi's list records legacy barriers; keep one kind on a shared queue
    nvrhi::DeviceHandle made = nvrhi::d3d12::createDevice(desc);
    if (!made) throw Error("NVRHI made no device");
    d->native = static_cast<nvrhi::d3d12::IDevice*>(made.Get());
    d->nv = d->debug ? nvrhi::validation::createValidationLayer(made) : made;  // QRhi's device too, when it has one
}

Device* Device::attach(uintptr_t device, uintptr_t queue) {
    if (!device || !queue) throw Error("no device or queue to take over");
    auto* d = new Device();
    d->d3d = reinterpret_cast<ID3D12Device*>(device);  // a reference of our own (ComPtr adds one)
    d->queue = reinterpret_cast<ID3D12CommandQueue*>(queue);
    d->d3d.As(&d->info_queue);  // there when QRhi made the device with the debug layer
    d->debug = d->info_queue != nullptr;
    make_nvrhi(d);
    return d;
}

Device* Device::create(bool debug, int adapter) {
    auto* d = new Device();
    d->owned = true;
    if (debug) {
        ComPtr<ID3D12Debug> layer;
        if (SUCCEEDED(D3D12GetDebugInterface(IID_PPV_ARGS(&layer)))) {  // only with Windows' Graphics Tools
            layer->EnableDebugLayer();
            d->debug = true;
        }
    }
    ComPtr<IDXGIFactory6> factory;
    if (FAILED(CreateDXGIFactory2(0, IID_PPV_ARGS(&factory)))) throw Error("no DXGI factory");
    ComPtr<IDXGIAdapter1> chosen;
    if (adapter == WARP) factory->EnumWarpAdapter(IID_PPV_ARGS(&chosen));  // software: no GPU needed (tests, CI)
    for (UINT i = 0; adapter != WARP && (adapter < 0 || UINT(adapter) >= i); i++) {
        ComPtr<IDXGIAdapter1> a;
        if (factory->EnumAdapterByGpuPreference(i, DXGI_GPU_PREFERENCE_HIGH_PERFORMANCE, IID_PPV_ARGS(&a)) ==
            DXGI_ERROR_NOT_FOUND)
            break;
        if (adapter < 0 || UINT(adapter) == i) {
            chosen = a;
            break;
        }
    }
    if (!chosen && (adapter >= 0 || adapter == WARP)) {  // an index past the adapters, or no software adapter: not the default one in its place
        delete d;
        throw Error(adapter == WARP ? "no software adapter" : "no adapter " + std::to_string(adapter));
    }
    HRESULT hr = D3D12CreateDevice(chosen.Get(), D3D_FEATURE_LEVEL_12_0, IID_PPV_ARGS(&d->d3d));
    if (FAILED(hr)) {
        delete d;
        throw Error("D3D12CreateDevice failed");
    }
    D3D12_COMMAND_QUEUE_DESC q{};
    q.Type = D3D12_COMMAND_LIST_TYPE_DIRECT;
    if (FAILED(d->d3d->CreateCommandQueue(&q, IID_PPV_ARGS(&d->queue)))) {
        delete d;
        throw Error("CreateCommandQueue failed");
    }
    if (d->debug) d->d3d.As(&d->info_queue);
    make_nvrhi(d);
    return d;
}

Device::~Device() {
    works.reset();  // its workers stopped and joined: they make pipelines on this device
    if (nv) nv->waitForIdle();
    std::lock_guard<std::mutex> hold(late_lock);
    late.clear();
    nv = nullptr;
}

void Device::release_late(nvrhi::IResource* handle) {
    std::lock_guard<std::mutex> hold(late_lock);
    late.emplace_back(frame, handle);
}

void Device::end_frame() {
    frame++;
    {
        std::lock_guard<std::mutex> hold(late_lock);
        while (!late.empty() && late.front().first + keep_frames <= frame) late.pop_front();
    }
    nv->runGarbageCollection();
}

void Device::wait_idle() {
    nv->waitForIdle();
    std::lock_guard<std::mutex> hold(late_lock);
    late.clear();
    nv->runGarbageCollection();
}

std::vector<std::string> Device::messages() {
    std::vector<std::string> out = log.take();
    if (info_queue) {
        UINT64 n = info_queue->GetNumStoredMessages();
        for (UINT64 i = 0; i < n && out.size() < 4096; i++) {
            SIZE_T size = 0;
            info_queue->GetMessage(i, nullptr, &size);
            std::vector<char> raw(size);
            auto* m = reinterpret_cast<D3D12_MESSAGE*>(raw.data());
            if (SUCCEEDED(info_queue->GetMessage(i, m, &size)))
                out.push_back(std::string("d3d12: ") + m->pDescription);  // its length counts the NUL
        }
        info_queue->ClearStoredMessages();
    }
    return out;
}

std::string Device::adapter_name() const {
    LUID luid = d3d->GetAdapterLuid();
    ComPtr<IDXGIFactory4> factory;
    ComPtr<IDXGIAdapter1> adapter;
    if (FAILED(CreateDXGIFactory2(0, IID_PPV_ARGS(&factory))) ||
        FAILED(factory->EnumAdapterByLuid(luid, IID_PPV_ARGS(&adapter))))
        return "";
    DXGI_ADAPTER_DESC1 desc;
    adapter->GetDesc1(&desc);
    std::wstring w(desc.Description);
    std::string out;
    for (wchar_t c : w) out.push_back(c < 128 ? char(c) : '?');
    return out;
}

std::pair<uint64_t, uint64_t> Device::video_memory() const {
    // this process's use of the adapter's own memory and the budget Windows gives it (native-backend.md 15)
    LUID luid = d3d->GetAdapterLuid();
    ComPtr<IDXGIFactory4> factory;
    ComPtr<IDXGIAdapter3> adapter;
    DXGI_QUERY_VIDEO_MEMORY_INFO info{};
    if (FAILED(CreateDXGIFactory2(0, IID_PPV_ARGS(&factory))) ||
        FAILED(factory->EnumAdapterByLuid(luid, IID_PPV_ARGS(&adapter))) ||
        FAILED(adapter->QueryVideoMemoryInfo(0, DXGI_MEMORY_SEGMENT_GROUP_LOCAL, &info)))
        return {0, 0};
    return {info.CurrentUsage, info.Budget};
}

int Device::shader_model() const {
    D3D12_FEATURE_DATA_SHADER_MODEL sm{D3D_SHADER_MODEL_6_6};
    while (FAILED(d3d->CheckFeatureSupport(D3D12_FEATURE_SHADER_MODEL, &sm, sizeof(sm))) &&
           sm.HighestShaderModel > D3D_SHADER_MODEL_5_1)
        sm.HighestShaderModel = D3D_SHADER_MODEL(int(sm.HighestShaderModel) - 1);
    return int(sm.HighestShaderModel);
}

int Device::binding_tier() const {
    D3D12_FEATURE_DATA_D3D12_OPTIONS o{};
    d3d->CheckFeatureSupport(D3D12_FEATURE_D3D12_OPTIONS, &o, sizeof(o));
    return int(o.ResourceBindingTier);
}

Timer* Device::timer() {
    auto* t = new Timer();
    t->device = this;
    t->handle = nv->createTimerQuery();
    return t;
}

float Device::timer_ms(Timer& t) {
    if (!nv->pollTimerQuery(t.get())) return -1.0f;
    float s = nv->getTimerQueryTime(t.get());
    nv->resetTimerQuery(t.get());
    return s * 1000.0f;
}

}  // namespace gpu
