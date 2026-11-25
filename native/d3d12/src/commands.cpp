// Command lists: uploads, clears, copies, draws and dispatches through NVRHI, and the draw recorded into a list of
// QRhi's (route A of docs/rendering/native-backend.md, step 0).
#include "gpu.h"

#include <d3d12/d3d12-backend.h>  // NVRHI's own types: the root signature and tables a foreign list is given

#include <cstring>

namespace gpu {

CommandList::CommandList(Device& device) : device_(device) {
    // not immediate: each scene keeps a list of its own open between its submits (DX11's immediate context rule,
    // which NVRHI's validation enforces on every backend, would allow one open list at a time)
    list_ = device.nv->createCommandList(nvrhi::CommandListParameters().setEnableImmediateExecution(false));
    if (!list_) throw Error("NVRHI made no command list");
}

void CommandList::open() {
    if (!open_) list_->open();
    open_ = true;
}

void CommandList::close() {
    if (open_) list_->close();
    open_ = false;
}

void CommandList::execute() {
    // a scene submits its list before each pass it records on QRhi's; most passes leave it empty (performance.md 4)
    if (!recorded_ && open_) return;
    close();
    device_.nv->executeCommandList(list_);
    recorded_ = false;
    open();
}

void CommandList::write_buffer(Buffer& b, const std::string& data, uint64_t offset) {
    if (!b.handle) throw Error("a write to a released buffer");
    if (offset + data.size() > b.size) throw Error("a write past the buffer's end");
    if (b.get()->getDesc().cpuAccess == nvrhi::CpuAccessMode::Write) {  // an upload buffer: written at once
        auto* p = static_cast<uint8_t*>(device_.nv->mapBuffer(b.get(), nvrhi::CpuAccessMode::Write));
        std::memcpy(p + offset, data.data(), data.size());
        device_.nv->unmapBuffer(b.get());
        return;
    }
    rec()->writeBuffer(b.get(), data.data(), data.size(), offset);
}

void CommandList::write_texture(Texture& t, uint32_t layer, uint32_t mip, const std::string& data, size_t row_pitch,
                                size_t depth_pitch) {
    rec()->writeTexture(t.get(), layer, mip, data.data(), row_pitch, depth_pitch);
}

void CommandList::clear_color(Texture& t, float r, float g, float b, float a) {
    rec()->clearTextureFloat(t.get(), nvrhi::AllSubresources, nvrhi::Color(r, g, b, a));
}

void CommandList::clear_depth(Texture& t, float depth, uint32_t stencil) {
    bool has_stencil = nvrhi::getFormatInfo(t.desc.format).hasStencil;
    rec()->clearDepthStencilTexture(t.get(), nvrhi::AllSubresources, true, depth, has_stencil, uint8_t(stencil));
}

void CommandList::clear_uint(Buffer& b, uint32_t value) { rec()->clearBufferUInt(b.get(), value); }

void CommandList::copy_to_staging(Staging& s, Texture& t) {
    rec()->copyTexture(s.get(), nvrhi::TextureSlice(), t.get(), nvrhi::TextureSlice());
}

void CommandList::copy_texture(Texture& dst, Texture& src) {
    rec()->copyTexture(dst.get(), nvrhi::TextureSlice(), src.get(), nvrhi::TextureSlice());
}

void CommandList::copy_region(Texture& dst, uint32_t dst_layer, uint32_t dst_mip, uint32_t x, uint32_t y,
                              Texture& src, uint32_t src_layer, uint32_t src_mip, uint32_t width, uint32_t height) {
    nvrhi::TextureSlice to;
    to.setArraySlice(dst_layer).setMipLevel(dst_mip).setOrigin(x, y, 0).setSize(width, height, 1);
    nvrhi::TextureSlice from;
    from.setArraySlice(src_layer).setMipLevel(src_mip).setOrigin(0, 0, 0).setSize(width, height, 1);
    rec()->copyTexture(dst.get(), to, src.get(), from);
}

void CommandList::transitions(const std::vector<std::tuple<uintptr_t, int, int>>& items) {
    if (!open_) throw Error("transitions on a closed command list");
    auto* native = static_cast<ID3D12GraphicsCommandList*>(
        rec()->getNativeObject(nvrhi::ObjectTypes::D3D12_GraphicsCommandList).pointer);
    if (!native) throw Error("the command list has no Direct3D 12 list");
    rec()->commitBarriers();  // NVRHI's own first: ours go between its commands so far and the next
    gpu::transitions(uintptr_t(native), items);
}

void CommandList::uav_barrier() {
    if (!open_) throw Error("a barrier on a closed command list");
    auto* native = static_cast<ID3D12GraphicsCommandList*>(
        rec()->getNativeObject(nvrhi::ObjectTypes::D3D12_GraphicsCommandList).pointer);
    if (!native) throw Error("the command list has no Direct3D 12 list");
    rec()->commitBarriers();
    gpu::uav_barrier(uintptr_t(native));
}

static nvrhi::ViewportState viewport_of(float x, float y, float w, float h) {
    nvrhi::ViewportState v;
    v.addViewportAndScissorRect(nvrhi::Viewport(x, x + w, y, y + h, 0.0f, 1.0f));
    return v;
}

void CommandList::draw(Framebuffer& fb, const Draw& d, bool indexed, float x, float y, float w, float h) {
    if (d.pipeline) d.pipeline->wait();  // one still made in the background: this list's draws need it now
    if (!d.pipeline || !d.pipeline->handle) throw Error("a released pipeline in a draw");
    if (!fb.handle) throw Error("a released framebuffer in a draw");
    for (const auto& v : d.vertex_buffers)
        if (!v.first || !v.first->handle) throw Error("a released buffer in a draw");
    if (d.index_buffer && !d.index_buffer->handle) throw Error("a released buffer in a draw");
    nvrhi::GraphicsState state;
    state.pipeline = d.pipeline->graphics();
    state.framebuffer = fb.get();
    state.viewport = viewport_of(x, y, w ? w : float(fb.width), h ? h : float(fb.height));
    for (auto* b : d.bindings) state.bindings.push_back(b);
    for (uint32_t i = 0; i < d.vertex_buffers.size(); i++)
        state.addVertexBuffer(nvrhi::VertexBufferBinding()
                                  .setBuffer(d.vertex_buffers[i].first->get())
                                  .setSlot(i)
                                  .setOffset(d.vertex_buffers[i].second));
    if (d.index_buffer)
        state.setIndexBuffer(nvrhi::IndexBufferBinding()
                                 .setBuffer(d.index_buffer->get())
                                 .setFormat(d.index32 ? nvrhi::Format::R32_UINT : nvrhi::Format::R16_UINT)
                                 .setOffset(uint32_t(d.index_offset)));
    rec()->setGraphicsState(state);
    if (!d.push.empty()) rec()->setPushConstants(d.push.data(), d.push.size());
    nvrhi::DrawArguments args;
    args.vertexCount = d.count;
    args.instanceCount = d.instances;
    args.startInstanceLocation = d.first_instance;
    if (indexed) {
        args.startIndexLocation = d.first;
        args.startVertexLocation = d.base_vertex;
        rec()->drawIndexed(args);
    } else {
        args.startVertexLocation = d.first;
        rec()->draw(args);
    }
}

void CommandList::dispatch(const Draw& d, uint32_t x, uint32_t y, uint32_t z) {
    if (d.pipeline) d.pipeline->wait();
    if (!d.pipeline || !d.pipeline->handle) throw Error("a released pipeline in a dispatch");
    nvrhi::ComputeState state;
    state.pipeline = d.pipeline->compute_pipeline();
    for (auto* b : d.bindings) state.bindings.push_back(b);
    rec()->setComputeState(state);
    if (!d.push.empty()) rec()->setPushConstants(d.push.data(), d.push.size());
    rec()->dispatch(x, y, z);
}

void CommandList::begin_timer(Timer& t) { rec()->beginTimerQuery(t.get()); }
void CommandList::end_timer(Timer& t) { rec()->endTimerQuery(t.get()); }

void CommandList::marker(const std::string& name, bool begin) {
    if (begin)
        rec()->beginMarker(name.c_str());
    else
        rec()->endMarker();
}

void CommandList::set_state(Texture& t, const std::string& state) {
    rec()->setTextureState(t.get(), nvrhi::AllSubresources, state_named(state));
    rec()->commitBarriers();
}

std::string read_staging(Device& device, Staging& s) {
    device.nv->waitForIdle();
    size_t pitch = 0;
    auto* p = static_cast<const uint8_t*>(
        device.nv->mapStagingTexture(s.get(), nvrhi::TextureSlice(), nvrhi::CpuAccessMode::Read, &pitch));
    if (!p) throw Error("the readback texture could not be mapped");
    const auto& info = nvrhi::getFormatInfo(s.desc.format);
    size_t row = size_t(s.desc.width) * info.bytesPerBlock;
    std::string out(row * s.desc.height, '\0');
    for (uint32_t y = 0; y < s.desc.height; y++) std::memcpy(&out[y * row], p + y * pitch, row);
    device.nv->unmapStagingTexture(s.get());
    return out;
}

static D3D_PRIMITIVE_TOPOLOGY topology_of(nvrhi::PrimitiveType t, uint32_t points) {
    switch (t) {
    case nvrhi::PrimitiveType::PatchList:
        return D3D_PRIMITIVE_TOPOLOGY(D3D_PRIMITIVE_TOPOLOGY_1_CONTROL_POINT_PATCHLIST + points - 1);
    case nvrhi::PrimitiveType::TriangleStrip: return D3D_PRIMITIVE_TOPOLOGY_TRIANGLESTRIP;
    case nvrhi::PrimitiveType::LineList: return D3D_PRIMITIVE_TOPOLOGY_LINELIST;
    case nvrhi::PrimitiveType::PointList: return D3D_PRIMITIVE_TOPOLOGY_POINTLIST;
    default: return D3D_PRIMITIVE_TOPOLOGY_TRIANGLELIST;
    }
}

namespace {

// What a foreign list holds from the draws recorded so far in one call, so that the next draw sets only what differs
// (an island's objects: thousands of draws over a few pipelines and the frame's tables)
struct Bound {
    static constexpr uint64_t UNSET = ~0ull;  // a parameter not set since the root signature (a null view is 0)
    ID3D12PipelineState* pso = nullptr;
    ID3D12RootSignature* root = nullptr;
    std::vector<uint64_t> params;  // per root parameter: the table's GPU handle or the view's address set
    std::string push;

    bool set(uint32_t index, uint64_t value) {
        if (index >= params.size()) params.resize(index + 1, UNSET);
        if (params[index] == value) return false;
        params[index] = value;
        return true;
    }
};

// The root parameters' setters of a graphics or a compute root signature, so that both bind through ``bind_root``
struct GraphicsRoot {
    ID3D12GraphicsCommandList* list;
    void table(UINT i, D3D12_GPU_DESCRIPTOR_HANDLE h) const { list->SetGraphicsRootDescriptorTable(i, h); }
    void view(UINT i, uint64_t at) const { list->SetGraphicsRootConstantBufferView(i, at); }
    void constants(UINT i, UINT n, const void* p) const { list->SetGraphicsRoot32BitConstants(i, n, p, 0); }
};

struct ComputeRoot {
    ID3D12GraphicsCommandList* list;
    void table(UINT i, D3D12_GPU_DESCRIPTOR_HANDLE h) const { list->SetComputeRootDescriptorTable(i, h); }
    void view(UINT i, uint64_t at) const { list->SetComputeRootConstantBufferView(i, at); }
    void constants(UINT i, UINT n, const void* p) const { list->SetComputeRoot32BitConstants(i, n, p, 0); }
};

struct Heaps {
    nvrhi::d3d12::IDescriptorHeap* views;
    nvrhi::d3d12::IDescriptorHeap* samplers;
};

Heaps set_heaps(Device& device, ID3D12GraphicsCommandList* list) {
    Heaps h{device.native->getDescriptorHeap(nvrhi::d3d12::DescriptorHeapType::ShaderResourceView),
            device.native->getDescriptorHeap(nvrhi::d3d12::DescriptorHeapType::Sampler)};
    ID3D12DescriptorHeap* heaps[2] = {h.views->getShaderVisibleHeap(), h.samplers->getShaderVisibleHeap()};
    list->SetDescriptorHeaps(2, heaps);  // QRhi sets its own again at endExternal
    return h;
}

void set_viewport(ID3D12GraphicsCommandList* list, float x, float y, float w, float h) {
    D3D12_VIEWPORT vp{x, y, w, h, 0.0f, 1.0f};
    D3D12_RECT scissor{LONG(x), LONG(y), LONG(x + w), LONG(y + h)};
    list->RSSetViewports(1, &vp);
    list->RSSetScissorRects(1, &scissor);
}

// The draw's sets and tables, its root views (``root_offset`` added to each: a draw's slot of one buffer, cbModel)
// and its push constants where they differ from ``bound``; the same for a graphics and a compute root signature
template <class Root>
void bind_root(const Root& set, const nvrhi::d3d12::RootSignature* root, const Heaps& heaps, const Draw& d,
               uint64_t root_offset, Bound& bound) {
    for (uint32_t i = 0; i < d.bindings.size() && i < root->pipelineLayouts.size(); i++) {
        uint32_t offset = root->pipelineLayouts[i].second;
        if (!d.bindings[i]->getDesc()) {  // a bindless table
            auto* t = static_cast<nvrhi::d3d12::DescriptorTable*>(d.bindings[i]);
            auto handle = heaps.views->getGpuHandle(t->firstDescriptor);
            if (bound.set(offset, handle.ptr)) set.table(offset, handle);
            continue;
        }
        auto* s = static_cast<nvrhi::d3d12::BindingSet*>(d.bindings[i]);
        for (const auto& [index, buffer] : s->rootParametersVolatileCB) {
            auto* b = static_cast<nvrhi::d3d12::Buffer*>(buffer);
            if (b && b->desc.isVolatile) throw Error("a volatile constant buffer on a foreign list: use a static one");
            uint64_t at = b ? b->gpuVA + root_offset : 0;
            if (bound.set(offset + index, at)) set.view(offset + index, at);
        }
        if (s->descriptorTableValidSamplers) {
            auto handle = heaps.samplers->getGpuHandle(s->descriptorTableSamplers);
            if (bound.set(offset + s->rootParameterIndexSamplers, handle.ptr))
                set.table(offset + s->rootParameterIndexSamplers, handle);
        }
        if (s->descriptorTableValidSRVetc) {
            auto handle = heaps.views->getGpuHandle(s->descriptorTableSRVetc);
            if (bound.set(offset + s->rootParameterIndexSRVetc, handle.ptr))
                set.table(offset + s->rootParameterIndexSRVetc, handle);
        }
    }
    if (d.push.empty() || !root->pushConstantByteSize || d.push == bound.push) return;
    if (d.push.size() % 4 || d.push.size() > root->pushConstantByteSize)
        throw Error("push constants of " + std::to_string(d.push.size()) + " bytes where the root signature takes " +
                    std::to_string(root->pushConstantByteSize));
    set.constants(root->rootParameterPushConstants, UINT(d.push.size() / 4), d.push.data());
    bound.push = d.push;
}

// A buffer of a draw by its address and the bytes from ``at`` on; a released one or an offset past its end raises
// (the draw would read freed or foreign memory)
std::pair<uint64_t, UINT> span_of(const Buffer* b, uint64_t at) {
    if (!b || !b->get()) throw Error("a released buffer in a draw");
    if (at > b->size) throw Error("a buffer offset past the buffer's end");
    return {b->gpu_address() + at, UINT(b->size - at)};
}

// One draw's state: the pipeline, its tables and root views where they differ from ``bound``, its vertex buffers and
// (``indexed``) its index buffer; false (nothing bound) while a worker still makes its pipeline: no frame waits for
// one (its part waits for all of its own)
bool bind_one(ID3D12GraphicsCommandList* list, const Heaps& heaps, const Draw& d, bool indexed, uint64_t root_offset,
              Bound& bound) {
    if (d.pipeline && !d.pipeline->ready() && d.pipeline->making) return false;
    if (!d.pipeline || !d.pipeline->handle)
        throw Error(d.pipeline && !d.pipeline->error.empty() ? d.pipeline->error : "a released pipeline in a draw");
    auto* pso = static_cast<nvrhi::d3d12::GraphicsPipeline*>(d.pipeline->graphics());
    auto* root = pso->rootSignature.Get();
    if (bound.root != root->handle.Get()) {
        list->SetGraphicsRootSignature(root->handle.Get());
        bound.root = root->handle.Get();
        bound.params.clear();  // a new root signature: every parameter is unset
        bound.push.clear();
    }
    if (bound.pso != pso->pipelineState.Get()) {
        list->SetPipelineState(pso->pipelineState.Get());
        list->IASetPrimitiveTopology(topology_of(pso->desc.primType, pso->desc.patchControlPoints));
        const auto& ds = pso->desc.renderState.depthStencilState;
        if (ds.stencilEnable) list->OMSetStencilRef(ds.stencilRefValue);  // QRhi sets its own per draw
        bound.pso = pso->pipelineState.Get();
    }
    bind_root(GraphicsRoot{list}, root, heaps, d, root_offset, bound);
    auto* input = static_cast<nvrhi::d3d12::InputLayout*>(pso->desc.inputLayout.Get());
    D3D12_VERTEX_BUFFER_VIEW vbs[D3D12_IA_VERTEX_INPUT_RESOURCE_SLOT_COUNT];
    if (d.vertex_buffers.size() > D3D12_IA_VERTEX_INPUT_RESOURCE_SLOT_COUNT) throw Error("too many vertex buffers");
    UINT streams = 0;
    for (uint32_t i = 0; i < d.vertex_buffers.size(); i++) {
        auto [address, bytes] = span_of(d.vertex_buffers[i].first, d.vertex_buffers[i].second);
        UINT stride = input && input->elementStrides.count(i) ? input->elementStrides[i] : 0;
        vbs[streams++] = {address, bytes, stride};
    }
    if (streams) list->IASetVertexBuffers(0, streams, vbs);
    if (indexed && d.index_buffer) {
        auto [address, bytes] = span_of(d.index_buffer, d.index_offset);
        D3D12_INDEX_BUFFER_VIEW ib{address, bytes, d.index32 ? DXGI_FORMAT_R32_UINT : DXGI_FORMAT_R16_UINT};
        list->IASetIndexBuffer(&ib);
    }
    return true;
}

// One draw: its state (``bind_one``) and the draw of ``instances``
void record_one(ID3D12GraphicsCommandList* list, const Heaps& heaps, const Draw& d, bool indexed, uint32_t instances,
                uint64_t root_offset, Bound& bound) {
    if (!bind_one(list, heaps, d, indexed, root_offset, bound)) return;
    if (indexed && d.index_buffer)
        list->DrawIndexedInstanced(d.count, instances, d.first, INT(d.base_vertex), d.first_instance);
    else
        list->DrawInstanced(d.count, instances, d.first, d.first_instance);
}

// The device's command signature of one indexed draw, made once (no root argument: no root signature named)
ID3D12CommandSignature* indexed_signature(Device& device) {
    if (!device.indexed_signature) {
        D3D12_INDIRECT_ARGUMENT_DESC argument{};
        argument.Type = D3D12_INDIRECT_ARGUMENT_TYPE_DRAW_INDEXED;
        D3D12_COMMAND_SIGNATURE_DESC desc{};
        desc.ByteStride = INDIRECT_STRIDE;
        desc.NumArgumentDescs = 1;
        desc.pArgumentDescs = &argument;
        if (FAILED(device.d3d->CreateCommandSignature(&desc, nullptr, IID_PPV_ARGS(&device.indexed_signature))))
            throw Error("the device made no command signature for indirect draws");
    }
    return device.indexed_signature.Get();
}

ID3D12GraphicsCommandList* foreign(uintptr_t list_ptr) {
    auto* list = reinterpret_cast<ID3D12GraphicsCommandList*>(list_ptr);
    if (!list) throw Error("no command list to record into");
    return list;
}

}  // namespace

void record_external(Device& device, uintptr_t list_ptr, const Draw& d, bool indexed, float x, float y, float w,
                     float h) {
    auto* list = foreign(list_ptr);
    Heaps heaps = set_heaps(device, list);
    set_viewport(list, x, y, w, h);
    Bound bound;
    record_one(list, heaps, d, indexed, d.instances, d.root_offset, bound);
}

void record_draws(Device& device, uintptr_t list_ptr, const std::vector<const Draw*>& draws,
                  const std::vector<uint32_t>& instances, const std::vector<uint64_t>& offsets, float x, float y,
                  float w, float h) {
    if (instances.size() != draws.size() || offsets.size() != draws.size())
        throw Error("record_draws takes one instance count and one root offset per draw");
    auto* list = foreign(list_ptr);
    Heaps heaps = set_heaps(device, list);
    set_viewport(list, x, y, w, h);
    Bound bound;
    for (size_t i = 0; i < draws.size(); i++) {
        if (!instances[i]) continue;
        record_one(list, heaps, *draws[i], draws[i]->index_buffer != nullptr, instances[i], offsets[i], bound);
    }
}

void record_indirect(Device& device, uintptr_t list_ptr, const std::vector<const Draw*>& draws, Buffer& args,
                     const std::vector<uint64_t>& args_offsets, const std::vector<uint64_t>& offsets, float x, float y,
                     float w, float h) {
    if (args_offsets.size() != draws.size() || offsets.size() != draws.size())
        throw Error("record_indirect takes one argument offset and one root offset per draw");
    if (!args.get()) throw Error("a released argument buffer in record_indirect");
    if (!args.get()->getDesc().isDrawIndirectArgs) throw Error("record_indirect's arguments are not an indirect buffer");
    auto* resource = reinterpret_cast<ID3D12Resource*>(args.native());
    auto* signature = indexed_signature(device);
    auto* list = foreign(list_ptr);
    Heaps heaps = set_heaps(device, list);
    set_viewport(list, x, y, w, h);
    Bound bound;
    for (size_t i = 0; i < draws.size(); i++) {
        if (!draws[i]->index_buffer) throw Error("record_indirect draws indexed draws only");
        // arguments past the buffer's end are a device removal, not an error the debug layer reports in time
        // The check avoids the sum: an offset near 2^64 would wrap it past the buffer's end.
        if (args_offsets[i] % 4 || args_offsets[i] > args.size || args.size - args_offsets[i] < INDIRECT_STRIDE)
            throw Error("an indirect draw's arguments lie past the buffer's end");
        if (!bind_one(list, heaps, *draws[i], true, offsets[i], bound)) continue;
        list->ExecuteIndirect(signature, 1, resource, args_offsets[i], nullptr, 0);
    }
}

void record_dispatch(Device& device, uintptr_t list_ptr, const Draw& d, uint32_t x, uint32_t y, uint32_t z) {
    if (!d.pipeline || !d.pipeline->compute) throw Error("record_dispatch takes a compute pipeline");
    d.pipeline->wait();
    if (!d.pipeline->handle) throw Error("a released pipeline in a dispatch");
    auto* list = foreign(list_ptr);
    Heaps heaps = set_heaps(device, list);
    auto* pso = static_cast<nvrhi::d3d12::ComputePipeline*>(d.pipeline->compute_pipeline());
    auto* root = pso->rootSignature.Get();
    list->SetComputeRootSignature(root->handle.Get());
    list->SetPipelineState(pso->pipelineState.Get());
    Bound bound;  // nothing set yet: every parameter is
    bind_root(ComputeRoot{list}, root, heaps, d, d.root_offset, bound);
    list->Dispatch(x, y, z);
}

void uav_barrier(uintptr_t list_ptr) {
    D3D12_RESOURCE_BARRIER b{};
    b.Type = D3D12_RESOURCE_BARRIER_TYPE_UAV;
    b.UAV.pResource = nullptr;
    foreign(list_ptr)->ResourceBarrier(1, &b);
}

void copy_buffer(uintptr_t list_ptr, uintptr_t dst, uint64_t dst_offset, uintptr_t src, uint64_t src_offset,
                 uint64_t size) {
    if (!dst || !src) throw Error("copy_buffer needs two resources");
    auto* to = reinterpret_cast<ID3D12Resource*>(dst);
    auto* from = reinterpret_cast<ID3D12Resource*>(src);
    // a copy past either end is a device removal, not an error the debug layer reports in time
    if (dst_offset + size > to->GetDesc().Width || src_offset + size > from->GetDesc().Width)
        throw Error("copy_buffer past a buffer's end");
    foreign(list_ptr)->CopyBufferRegion(to, dst_offset, from, src_offset, size);
}

void copy_texture(uintptr_t list_ptr, uintptr_t dst, uintptr_t src) {
    if (!dst || !src) throw Error("copy_texture needs two resources");
    auto* to = reinterpret_cast<ID3D12Resource*>(dst);
    auto* from = reinterpret_cast<ID3D12Resource*>(src);
    const D3D12_RESOURCE_DESC a = to->GetDesc(), b = from->GetDesc();
    // CopyResource of unlike textures is a device removal, not an error the debug layer reports in time
    if (a.Dimension != b.Dimension || a.Width != b.Width || a.Height != b.Height ||
        a.DepthOrArraySize != b.DepthOrArraySize || a.MipLevels != b.MipLevels || a.Format != b.Format)
        throw Error("copy_texture between unlike textures");
    foreign(list_ptr)->CopyResource(to, from);
}

}  // namespace gpu
