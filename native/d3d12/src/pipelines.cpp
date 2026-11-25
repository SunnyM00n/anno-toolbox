// Pipelines made as the game makes them: on worker threads where the caller asks for it, and kept in the driver's
// pipeline library, whose bytes the caller keeps on disk so that a later start loads them instead of compiling
// (docs/rendering/performance.md section 4, item 8). Workers touch Direct3D 12's own objects only: NVRHI's caches
// are not locked, so its root signatures and the pipelines' handles are made on the caller's thread.
#include "gpu.h"

#include <common/dxgi-format.h>
#include <d3d12/d3d12-backend.h>

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstring>
#include <map>
#include <thread>

#ifndef MODULE_VERSION
#define MODULE_VERSION "0"
#endif
#ifndef NVRHI_REVISION
#define NVRHI_REVISION "unknown"
#endif

namespace gpu {

namespace {

constexpr char MAGIC[4] = {'N', 'G', 'P', 'L'};
constexpr uint32_t FORMAT = 1;  // the file's own layout: a header (below), then the library's bytes
enum : int { WAITING = 0, DONE = 1, FAILED = 2 };

// A 128-bit name of everything a pipeline is made from: two FNV-1a 64 over the same bytes, apart by their seeds
struct Digest {
    uint64_t a = 0xcbf29ce484222325ull, b = 0x84222325cbf29ce4ull;
    void bytes(const void* data, size_t size) {
        auto* p = static_cast<const uint8_t*>(data);
        for (size_t i = 0; i < size; i++) {
            a = (a ^ p[i]) * 0x100000001b3ull;
            b = (b ^ p[i]) * 0x100000001b3ull + 1;
        }
    }
    template <class T>
    void value(const T& v) {
        bytes(&v, sizeof(v));
    }
    void text(const std::string& s) {
        value(uint64_t(s.size()));
        bytes(s.data(), s.size());
    }
    std::wstring name() const {
        wchar_t out[33];
        swprintf(out, 33, L"%016llx%016llx", (unsigned long long)a, (unsigned long long)b);
        return out;
    }
};

double ms_since(std::chrono::steady_clock::time_point start) {
    return std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
}

}  // namespace

// What a worker needs to make one pipeline, copied out of NVRHI's objects, and what it made
struct PipelineJob {
    bool compute = false;
    std::string vs, hs, ds, ps, cs;           // the stages' bytecode
    std::vector<std::string> semantics;        // the input elements' names, which ``elements`` point into
    std::vector<D3D12_INPUT_ELEMENT_DESC> elements;
    D3D12_GRAPHICS_PIPELINE_STATE_DESC graphics{};
    ComPtr<ID3D12RootSignature> root;
    std::wstring name;                         // its name in the library
    std::atomic<int> state{WAITING};
    ComPtr<ID3D12PipelineState> made;
    std::string error;
};

// The caller's side of a pipeline being made: NVRHI's description and root signature, wrapped once the job is done
struct PipelineMaking {
    std::shared_ptr<PipelineJob> job;
    nvrhi::GraphicsPipelineDesc graphics;
    nvrhi::FramebufferInfo targets;
    nvrhi::ComputePipelineDesc compute;
    nvrhi::d3d12::RootSignatureHandle root;
};

struct PipelineWorks {
    Device& device;
    ComPtr<ID3D12Device1> d3d;
    ComPtr<ID3D12PipelineLibrary> library;  // none where the driver offers none
    std::string blob;                        // the bytes the library was opened from: it reads them while it lives
    std::string key;                         // what a file's library must have been made for
    bool opened = false;
    std::mutex library_lock;                 // its loads, stores and serialising, one at a time
    std::atomic<bool> changed{false};
    std::atomic<uint32_t> loaded{0}, made{0}, failed{0}, stored{0};
    std::atomic<int> making{0};  // jobs queued or on a worker
    std::atomic<uint64_t> load_us{0}, make_us{0};
    // callers only, never a worker: the root signatures by their layouts (kept, so the layouts' addresses stay theirs)
    std::map<std::vector<const void*>, nvrhi::d3d12::RootSignatureHandle> roots;
    std::mutex roots_lock;
    std::mutex lock;
    std::condition_variable wake;
    std::deque<std::shared_ptr<PipelineJob>> queue;
    std::vector<std::thread> threads;
    bool stop = false;

    explicit PipelineWorks(Device& d) : device(d) {
        d.d3d.As(&d3d);
        key = d.adapter_id() + " " + d.driver_version() + " " MODULE_VERSION " " NVRHI_REVISION;
        reset_library();
    }

    ~PipelineWorks() {
        {
            std::lock_guard<std::mutex> hold(lock);
            stop = true;
            for (auto& job : queue) {
                job->error = "the device went away";
                job->state.store(FAILED, std::memory_order_release);
            }
            queue.clear();
        }
        wake.notify_all();
        for (auto& t : threads) t.join();
    }

    void reset_library() {
        library.Reset();
        if (d3d) d3d->CreatePipelineLibrary(nullptr, 0, IID_PPV_ARGS(&library));  // none: made without one
    }

    // the workers, started with the first job: a quarter of the processor's threads, 1 to 4 [ours]
    void submit(const std::shared_ptr<PipelineJob>& job) {
        {
            std::lock_guard<std::mutex> hold(lock);
            if (threads.empty()) {
                unsigned n = std::max(1u, std::min(4u, std::thread::hardware_concurrency() / 4));
                for (unsigned i = 0; i < n; i++) threads.emplace_back([this] { run(); });
            }
            queue.push_back(job);
            making++;
        }
        wake.notify_one();
    }

    void run() {
        for (;;) {
            std::shared_ptr<PipelineJob> job;
            {
                std::unique_lock<std::mutex> hold(lock);
                wake.wait(hold, [this] { return stop || !queue.empty(); });
                if (stop) return;
                job = queue.front();
                queue.pop_front();
            }
            make(*job);
            making--;
        }
    }

    // a job's pipeline from the library, else made by the driver and stored; on any thread
    void make(PipelineJob& job) {
        auto start = std::chrono::steady_clock::now();
        D3D12_COMPUTE_PIPELINE_STATE_DESC compute{};
        auto stage = [](const std::string& code) { return D3D12_SHADER_BYTECODE{code.data(), code.size()}; };
        if (job.compute) {
            compute.pRootSignature = job.root.Get();
            compute.CS = stage(job.cs);
        } else {
            auto& g = job.graphics;
            g.pRootSignature = job.root.Get();
            if (!job.vs.empty()) g.VS = stage(job.vs);
            if (!job.hs.empty()) g.HS = stage(job.hs);
            if (!job.ds.empty()) g.DS = stage(job.ds);
            if (!job.ps.empty()) g.PS = stage(job.ps);
            for (size_t i = 0; i < job.elements.size(); i++) job.elements[i].SemanticName = job.semantics[i].c_str();
            g.InputLayout = {job.elements.empty() ? nullptr : job.elements.data(), UINT(job.elements.size())};
        }
        HRESULT hr = E_FAIL;
        {
            std::lock_guard<std::mutex> hold(library_lock);
            if (library)
                hr = job.compute ? library->LoadComputePipeline(job.name.c_str(), &compute, IID_PPV_ARGS(&job.made))
                                 : library->LoadGraphicsPipeline(job.name.c_str(), &job.graphics,
                                                                 IID_PPV_ARGS(&job.made));
        }
        if (SUCCEEDED(hr)) {
            loaded++;
            load_us += uint64_t(ms_since(start) * 1000.0);
        } else {
            hr = job.compute ? device.d3d->CreateComputePipelineState(&compute, IID_PPV_ARGS(&job.made))
                             : device.d3d->CreateGraphicsPipelineState(&job.graphics, IID_PPV_ARGS(&job.made));
            if (FAILED(hr)) {
                failed++;
                job.error = std::string("the ") + (job.compute ? "compute" : "graphics") +
                            " pipeline was not made (the debug layer says why, where it is installed)";
                job.state.store(FAILED, std::memory_order_release);
                return;
            }
            made++;
            make_us += uint64_t(ms_since(start) * 1000.0);
            std::lock_guard<std::mutex> hold(library_lock);
            if (library && SUCCEEDED(library->StorePipeline(job.name.c_str(), job.made.Get()))) {  // a name kept: refused
                stored++;
                changed = true;
            }
        }
        job.state.store(DONE, std::memory_order_release);
    }

    // the root signature of ``layouts`` (one per set of layouts: NVRHI's own cache is private to it)
    nvrhi::d3d12::RootSignatureHandle root_of(const nvrhi::BindingLayoutVector& layouts, bool input) {
        std::lock_guard<std::mutex> hold(roots_lock);  // a caller that let Python's lock go may meet another
        std::vector<const void*> key_of{input ? this : nullptr};
        for (const auto& l : layouts) key_of.push_back(l.Get());
        auto& found = roots[key_of];
        if (!found) found = device.native->buildRootSignature(layouts, input, false);
        if (!found) throw Error("the root signature was not made");
        return found;
    }
};

PipelineWorks& Device::pipeline_works() {
    if (!works) works = std::make_shared<PipelineWorks>(*this);
    return *works;
}

std::string Device::adapter_id() const {
    LUID luid = d3d->GetAdapterLuid();
    ComPtr<IDXGIFactory4> factory;
    ComPtr<IDXGIAdapter1> adapter;
    if (FAILED(CreateDXGIFactory2(0, IID_PPV_ARGS(&factory))) ||
        FAILED(factory->EnumAdapterByLuid(luid, IID_PPV_ARGS(&adapter))))
        return "unknown";
    DXGI_ADAPTER_DESC1 desc;
    adapter->GetDesc1(&desc);
    char out[48];
    snprintf(out, sizeof(out), "%04x-%04x-%08x-%02x", desc.VendorId, desc.DeviceId, desc.SubSysId, desc.Revision);
    return out;
}

std::string Device::driver_version() const {
    LUID luid = d3d->GetAdapterLuid();
    ComPtr<IDXGIFactory4> factory;
    ComPtr<IDXGIAdapter1> adapter;
    LARGE_INTEGER version{};
    if (FAILED(CreateDXGIFactory2(0, IID_PPV_ARGS(&factory))) ||
        FAILED(factory->EnumAdapterByLuid(luid, IID_PPV_ARGS(&adapter))) ||
        FAILED(adapter->CheckInterfaceSupport(__uuidof(IDXGIDevice), &version)))
        return "unknown";
    char out[48];
    snprintf(out, sizeof(out), "%u.%u.%u.%u", unsigned(version.HighPart >> 16), unsigned(version.HighPart & 0xffff),
             unsigned(uint32_t(version.LowPart) >> 16), unsigned(version.LowPart & 0xffff));
    return out;
}

namespace {

void digest_layouts(Digest& d, const nvrhi::BindingLayoutVector& layouts, bool input) {
    d.value(uint8_t(input));
    for (const auto& l : layouts) {
        if (const auto* desc = l->getDesc()) {
            d.value(desc->visibility);
            d.value(desc->registerSpace);
            for (const auto& item : desc->bindings) {
                d.value(uint32_t(item.slot));
                d.value(uint32_t(item.type));
                d.value(uint32_t(item.size));
            }
        } else if (const auto* bindless = l->getBindlessDesc()) {
            d.value(bindless->visibility);
            d.value(bindless->firstSlot);
            d.value(bindless->maxCapacity);
            for (const auto& item : bindless->registerSpaces) {
                d.value(uint32_t(item.slot));
                d.value(uint32_t(item.type));
            }
        }
        d.value(uint8_t(0xff));  // the end of a layout
    }
}

std::string bytecode_of(nvrhi::IShader* shader) {
    if (!shader) return {};
    const auto& code = static_cast<nvrhi::d3d12::Shader*>(shader)->bytecode;
    return std::string(code.begin(), code.end());
}

// Direct3D 12's description of a graphics pipeline, as NVRHI fills it (d3d12-graphics.cpp createPipelineState)
void fill_graphics(Device& device, const nvrhi::GraphicsPipelineDesc& desc, const nvrhi::FramebufferInfo& targets,
                   PipelineJob& job) {
    auto& g = job.graphics;
    nvrhi::d3d12::TranslateBlendState(desc.renderState.blendState, g.BlendState);
    const auto& depth = desc.renderState.depthStencilState;
    nvrhi::d3d12::TranslateDepthStencilState(depth, g.DepthStencilState);
    if ((depth.depthTestEnable || depth.stencilEnable) && targets.depthFormat == nvrhi::Format::UNKNOWN) {
        g.DepthStencilState.DepthEnable = FALSE;
        g.DepthStencilState.StencilEnable = FALSE;
        device.log.message(nvrhi::MessageSeverity::Warning,
                           "depthEnable or stencilEnable is true, but no depth target is bound");
    }
    nvrhi::d3d12::TranslateRasterizerState(desc.renderState.rasterState, g.RasterizerState);
    switch (desc.primType) {
    case nvrhi::PrimitiveType::PointList: g.PrimitiveTopologyType = D3D12_PRIMITIVE_TOPOLOGY_TYPE_POINT; break;
    case nvrhi::PrimitiveType::LineList: g.PrimitiveTopologyType = D3D12_PRIMITIVE_TOPOLOGY_TYPE_LINE; break;
    case nvrhi::PrimitiveType::PatchList: g.PrimitiveTopologyType = D3D12_PRIMITIVE_TOPOLOGY_TYPE_PATCH; break;
    default: g.PrimitiveTopologyType = D3D12_PRIMITIVE_TOPOLOGY_TYPE_TRIANGLE; break;
    }
    g.DSVFormat = nvrhi::getDxgiFormatMapping(targets.depthFormat).rtvFormat;
    g.SampleDesc.Count = targets.sampleCount;
    g.SampleDesc.Quality = targets.sampleQuality;
    g.NumRenderTargets = UINT(std::min<size_t>(targets.colorFormats.size(), 8));
    for (UINT i = 0; i < g.NumRenderTargets; i++)
        g.RTVFormats[i] = nvrhi::getDxgiFormatMapping(targets.colorFormats[i]).rtvFormat;
    g.SampleMask = ~0u;
    job.vs = bytecode_of(desc.VS);
    job.hs = bytecode_of(desc.HS);
    job.ds = bytecode_of(desc.DS);
    job.ps = bytecode_of(desc.PS);
    if (auto* input = static_cast<nvrhi::d3d12::InputLayout*>(desc.inputLayout.Get())) {
        job.elements = input->inputElements;
        for (const auto& e : job.elements) job.semantics.emplace_back(e.SemanticName ? e.SemanticName : "");
    }
}

std::wstring name_of(const PipelineJob& job, const nvrhi::BindingLayoutVector& layouts, bool input) {
    Digest d;
    d.value(uint8_t(job.compute));
    for (const auto* code : {&job.vs, &job.hs, &job.ds, &job.ps, &job.cs}) d.text(*code);
    for (size_t i = 0; i < job.elements.size(); i++) {
        const auto& e = job.elements[i];
        d.text(job.semantics[i]);
        d.value(e.SemanticIndex);
        d.value(e.Format);
        d.value(e.InputSlot);
        d.value(e.AlignedByteOffset);
        d.value(e.InputSlotClass);
        d.value(e.InstanceDataStepRate);
    }
    if (!job.compute) {
        const auto& g = job.graphics;  // zeroed before it was filled: its padding is zero too
        d.value(g.BlendState);
        d.value(g.RasterizerState);
        d.value(g.DepthStencilState);
        d.value(g.SampleMask);
        d.value(g.PrimitiveTopologyType);
        d.value(g.NumRenderTargets);
        d.value(g.RTVFormats);
        d.value(g.DSVFormat);
        d.value(g.SampleDesc);
    }
    digest_layouts(d, layouts, input);
    return d.name();
}

// the job queued (``background``) or made here, its pipeline wrapped once it is done (``Pipeline::ready``)
Pipeline* start(Device& device, std::shared_ptr<PipelineMaking> making, bool compute, bool background) {
    auto& works = device.pipeline_works();
    auto* p = new Pipeline();
    p->device = &device;
    p->compute = compute;
    p->making = making;
    if (background) {
        works.submit(making->job);
        return p;
    }
    works.make(*making->job);
    try {
        p->wait();
    } catch (...) {
        delete p;
        throw;
    }
    return p;
}

}  // namespace

Pipeline* make_graphics(Device& device, const nvrhi::GraphicsPipelineDesc& desc, const nvrhi::FramebufferInfo& targets,
                        bool background) {
    auto making = std::make_shared<PipelineMaking>();
    making->graphics = desc;
    making->targets = targets;
    bool input = desc.inputLayout != nullptr;
    making->root = device.pipeline_works().root_of(desc.bindingLayouts, input);
    auto job = making->job = std::make_shared<PipelineJob>();
    fill_graphics(device, desc, targets, *job);
    job->root = static_cast<nvrhi::d3d12::RootSignature*>(making->root.Get())->handle.Get();
    job->name = name_of(*job, desc.bindingLayouts, input);
    return start(device, making, false, background);
}

Pipeline* make_compute(Device& device, const nvrhi::ComputePipelineDesc& desc, bool background) {
    auto making = std::make_shared<PipelineMaking>();
    making->compute = desc;
    making->root = device.pipeline_works().root_of(desc.bindingLayouts, false);
    auto job = making->job = std::make_shared<PipelineJob>();
    job->compute = true;
    job->cs = bytecode_of(desc.CS);
    job->root = static_cast<nvrhi::d3d12::RootSignature*>(making->root.Get())->handle.Get();
    job->name = name_of(*job, desc.bindingLayouts, false);
    return start(device, making, true, background);
}

bool Pipeline::ready() {
    if (handle) return true;
    if (!making) return false;  // released, or failed (``error``)
    auto& job = *making->job;
    int state = job.state.load(std::memory_order_acquire);
    if (state == WAITING) return false;
    auto root = static_cast<nvrhi::d3d12::RootSignature*>(making->root.Get());
    if (state == FAILED) {
        error = job.error;
    } else if (compute) {
        auto* made = new nvrhi::d3d12::ComputePipeline();
        made->desc = making->compute;
        made->rootSignature = root;
        made->pipelineState = job.made.Get();
        handle = nvrhi::ComputePipelineHandle::Create(made);
    } else {
        handle = device->native->createHandleForNativeGraphicsPipeline(root, job.made.Get(), making->graphics,
                                                                       making->targets);
        if (!handle) error = "the graphics pipeline was not wrapped";
    }
    making.reset();
    return handle != nullptr;
}

void Pipeline::wait() {
    while (making && making->job->state.load(std::memory_order_acquire) == WAITING) std::this_thread::yield();
    if (!ready() && !error.empty()) throw Error(error);
}

void Pipeline::release() {
    making.reset();  // a worker still making it finishes and its pipeline goes with the job
    Held::release();
}

bool open_pipeline_library(Device& device, const std::string& data) {
    auto& works = device.pipeline_works();
    std::lock_guard<std::mutex> hold(works.library_lock);
    if (!works.library || works.stored || works.opened) return false;  // only before a pipeline was stored
    size_t at = sizeof(MAGIC) + sizeof(uint32_t) * 2;
    if (data.size() < at || std::memcmp(data.data(), MAGIC, sizeof(MAGIC)) != 0) return false;
    uint32_t format = 0, length = 0;
    std::memcpy(&format, data.data() + 4, 4);
    std::memcpy(&length, data.data() + 8, 4);
    if (format != FORMAT || data.size() < at + length || data.compare(at, length, works.key) != 0) return false;
    std::string blob = data.substr(at + length);
    ComPtr<ID3D12PipelineLibrary> library;
    // a library of another driver or adapter is refused by Direct3D itself: the empty one stays
    if (FAILED(works.d3d->CreatePipelineLibrary(blob.data(), blob.size(), IID_PPV_ARGS(&library)))) return false;
    works.blob.swap(blob);
    works.library = library;
    works.opened = true;
    return true;
}

std::string pipeline_library_data(Device& device) {
    auto& works = device.pipeline_works();
    std::lock_guard<std::mutex> hold(works.library_lock);
    if (!works.library) return {};
    std::string out(MAGIC, sizeof(MAGIC));
    uint32_t length = uint32_t(works.key.size());
    out.append(reinterpret_cast<const char*>(&FORMAT), 4);
    out.append(reinterpret_cast<const char*>(&length), 4);
    out += works.key;
    size_t size = works.library->GetSerializedSize();
    size_t head = out.size();
    out.resize(head + size);
    if (FAILED(works.library->Serialize(out.data() + head, size))) return {};
    works.changed = false;
    return out;
}

PipelineStats pipeline_stats(Device& device) {
    auto& w = device.pipeline_works();
    PipelineStats s;
    s.loaded = w.loaded;
    s.made = w.made;
    s.failed = w.failed;
    s.stored = w.stored;
    s.load_ms = double(w.load_us) / 1000.0;
    s.make_ms = double(w.make_us) / 1000.0;
    s.changed = w.changed;
    s.pending = uint32_t(std::max(0, int(w.making)));
    std::lock_guard<std::mutex> hold(w.library_lock);
    s.library = w.library != nullptr;
    s.opened = w.opened;
    return s;
}

}  // namespace gpu
