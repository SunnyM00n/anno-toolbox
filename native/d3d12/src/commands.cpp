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
    close();
    device_.nv->executeCommandList(list_);
    open();
}

void CommandList::write_buffer(Buffer& b, const std::string& data, uint64_t offset) {
    if (offset + data.size() > b.size) throw Error("a write past the buffer's end");
    if (b.get()->getDesc().cpuAccess == nvrhi::CpuAccessMode::Write) {  // an upload buffer: written at once
        auto* p = static_cast<uint8_t*>(device_.nv->mapBuffer(b.get(), nvrhi::CpuAccessMode::Write));
        std::memcpy(p + offset, data.data(), data.size());
        device_.nv->unmapBuffer(b.get());
        return;
    }
    list_->writeBuffer(b.get(), data.data(), data.size(), offset);
}

void CommandList::write_texture(Texture& t, uint32_t layer, uint32_t mip, const std::string& data, size_t row_pitch,
                                size_t depth_pitch) {
    list_->writeTexture(t.get(), layer, mip, data.data(), row_pitch, depth_pitch);
}

void CommandList::clear_color(Texture& t, float r, float g, float b, float a) {
    list_->clearTextureFloat(t.get(), nvrhi::AllSubresources, nvrhi::Color(r, g, b, a));
}

void CommandList::clear_depth(Texture& t, float depth, uint32_t stencil) {
    bool has_stencil = nvrhi::getFormatInfo(t.desc.format).hasStencil;
    list_->clearDepthStencilTexture(t.get(), nvrhi::AllSubresources, true, depth, has_stencil, uint8_t(stencil));
}

void CommandList::clear_uint(Buffer& b, uint32_t value) { list_->clearBufferUInt(b.get(), value); }

void CommandList::copy_to_staging(Staging& s, Texture& t) {
    list_->copyTexture(s.get(), nvrhi::TextureSlice(), t.get(), nvrhi::TextureSlice());
}

void CommandList::copy_texture(Texture& dst, Texture& src) {
    list_->copyTexture(dst.get(), nvrhi::TextureSlice(), src.get(), nvrhi::TextureSlice());
}

void CommandList::copy_region(Texture& dst, uint32_t dst_layer, uint32_t dst_mip, uint32_t x, uint32_t y,
                              Texture& src, uint32_t src_layer, uint32_t src_mip, uint32_t width, uint32_t height) {
    nvrhi::TextureSlice to;
    to.setArraySlice(dst_layer).setMipLevel(dst_mip).setOrigin(x, y, 0).setSize(width, height, 1);
    nvrhi::TextureSlice from;
    from.setArraySlice(src_layer).setMipLevel(src_mip).setOrigin(0, 0, 0).setSize(width, height, 1);
    list_->copyTexture(dst.get(), to, src.get(), from);
}

void CommandList::transitions(const std::vector<std::tuple<uintptr_t, int, int>>& items) {
    if (!open_) throw Error("transitions on a closed command list");
    auto* native = static_cast<ID3D12GraphicsCommandList*>(
        list_->getNativeObject(nvrhi::ObjectTypes::D3D12_GraphicsCommandList).pointer);
    if (!native) throw Error("the command list has no Direct3D 12 list");
    list_->commitBarriers();  // NVRHI's own first: ours go between its commands so far and the next
    gpu::transitions(uintptr_t(native), items);
}

static nvrhi::ViewportState viewport_of(float x, float y, float w, float h) {
    nvrhi::ViewportState v;
    v.addViewportAndScissorRect(nvrhi::Viewport(x, x + w, y, y + h, 0.0f, 1.0f));
    return v;
}

void CommandList::draw(Framebuffer& fb, const Draw& d, bool indexed, float x, float y, float w, float h) {
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
    list_->setGraphicsState(state);
    if (!d.push.empty()) list_->setPushConstants(d.push.data(), d.push.size());
    nvrhi::DrawArguments args;
    args.vertexCount = d.count;
    args.instanceCount = d.instances;
    args.startInstanceLocation = d.first_instance;
    if (indexed) {
        args.startIndexLocation = d.first;
        args.startVertexLocation = d.base_vertex;
        list_->drawIndexed(args);
    } else {
        args.startVertexLocation = d.first;
        list_->draw(args);
    }
}

void CommandList::dispatch(const Draw& d, uint32_t x, uint32_t y, uint32_t z) {
    nvrhi::ComputeState state;
    state.pipeline = d.pipeline->compute_pipeline();
    for (auto* b : d.bindings) state.bindings.push_back(b);
    list_->setComputeState(state);
    if (!d.push.empty()) list_->setPushConstants(d.push.data(), d.push.size());
    list_->dispatch(x, y, z);
}

void CommandList::begin_timer(Timer& t) { list_->beginTimerQuery(t.get()); }
void CommandList::end_timer(Timer& t) { list_->endTimerQuery(t.get()); }

void CommandList::marker(const std::string& name, bool begin) {
    if (begin)
        list_->beginMarker(name.c_str());
    else
        list_->endMarker();
}

void CommandList::set_state(Texture& t, const std::string& state) {
    list_->setTextureState(t.get(), nvrhi::AllSubresources, state_named(state));
    list_->commitBarriers();
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

void record_external(Device& device, uintptr_t list_ptr, const Draw& d, bool indexed, float x, float y, float w,
                     float h) {
    auto* list = reinterpret_cast<ID3D12GraphicsCommandList*>(list_ptr);
    if (!list) throw Error("no command list to record into");
    auto* pso = static_cast<nvrhi::d3d12::GraphicsPipeline*>(d.pipeline->graphics());
    auto* root = pso->rootSignature.Get();
    auto* views = device.native->getDescriptorHeap(nvrhi::d3d12::DescriptorHeapType::ShaderResourceView);
    auto* samplers = device.native->getDescriptorHeap(nvrhi::d3d12::DescriptorHeapType::Sampler);
    ID3D12DescriptorHeap* heaps[2] = {views->getShaderVisibleHeap(), samplers->getShaderVisibleHeap()};
    list->SetDescriptorHeaps(2, heaps);  // QRhi sets its own again at endExternal
    list->SetGraphicsRootSignature(root->handle.Get());
    list->SetPipelineState(pso->pipelineState.Get());
    list->IASetPrimitiveTopology(topology_of(pso->desc.primType, pso->desc.patchControlPoints));
    for (uint32_t i = 0; i < d.bindings.size() && i < root->pipelineLayouts.size(); i++) {
        uint32_t offset = root->pipelineLayouts[i].second;
        if (!d.bindings[i]->getDesc()) {  // a bindless table
            auto* t = static_cast<nvrhi::d3d12::DescriptorTable*>(d.bindings[i]);
            list->SetGraphicsRootDescriptorTable(offset, views->getGpuHandle(t->firstDescriptor));
            continue;
        }
        auto* s = static_cast<nvrhi::d3d12::BindingSet*>(d.bindings[i]);
        for (const auto& [index, buffer] : s->rootParametersVolatileCB) {
            auto* b = static_cast<nvrhi::d3d12::Buffer*>(buffer);
            if (b && b->desc.isVolatile) throw Error("a volatile constant buffer on a foreign list: use a static one");
            list->SetGraphicsRootConstantBufferView(offset + index, b ? b->gpuVA : 0);
        }
        if (s->descriptorTableValidSamplers)
            list->SetGraphicsRootDescriptorTable(offset + s->rootParameterIndexSamplers,
                                                 samplers->getGpuHandle(s->descriptorTableSamplers));
        if (s->descriptorTableValidSRVetc)
            list->SetGraphicsRootDescriptorTable(offset + s->rootParameterIndexSRVetc,
                                                 views->getGpuHandle(s->descriptorTableSRVetc));
    }
    if (!d.push.empty() && root->pushConstantByteSize)
        list->SetGraphicsRoot32BitConstants(root->rootParameterPushConstants, UINT(d.push.size() / 4), d.push.data(),
                                            0);
    D3D12_VIEWPORT vp{x, y, w, h, 0.0f, 1.0f};
    D3D12_RECT scissor{LONG(x), LONG(y), LONG(x + w), LONG(y + h)};
    list->RSSetViewports(1, &vp);
    list->RSSetScissorRects(1, &scissor);
    auto* input = static_cast<nvrhi::d3d12::InputLayout*>(pso->desc.inputLayout.Get());
    std::vector<D3D12_VERTEX_BUFFER_VIEW> vbs;
    for (uint32_t i = 0; i < d.vertex_buffers.size(); i++) {
        auto [b, at] = d.vertex_buffers[i];
        UINT stride = input && input->elementStrides.count(i) ? input->elementStrides[i] : 0;
        vbs.push_back({b->gpu_address() + at, UINT(b->size - at), stride});
    }
    if (!vbs.empty()) list->IASetVertexBuffers(0, UINT(vbs.size()), vbs.data());
    if (indexed && d.index_buffer) {
        D3D12_INDEX_BUFFER_VIEW ib{d.index_buffer->gpu_address() + d.index_offset,
                                   UINT(d.index_buffer->size - d.index_offset),
                                   d.index32 ? DXGI_FORMAT_R32_UINT : DXGI_FORMAT_R16_UINT};
        list->IASetIndexBuffer(&ib);
        list->DrawIndexedInstanced(d.count, d.instances, d.first, INT(d.base_vertex), d.first_instance);
    } else {
        list->DrawInstanced(d.count, d.instances, d.first, d.first_instance);
    }
}

}  // namespace gpu
