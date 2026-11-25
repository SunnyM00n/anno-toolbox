// Buffers, textures, samplers, binding layouts and sets, shaders, input layouts and pipelines' descriptions (made in
// pipelines.cpp).
#include "gpu.h"

#include <d3d12/d3d12-backend.h>  // NVRHI's own types: the descriptors and input elements set here

#include <map>
#include <sstream>

namespace gpu {

static bool has(const std::string& list, const char* word) {
    std::stringstream in(list);
    std::string item;
    while (std::getline(in, item, ','))
        if (item == word) return true;
    return false;
}

Buffer* Device::buffer(uint64_t size, const std::string& usage, uint32_t stride, const std::string& format,
                       const std::string& name) {
    nvrhi::BufferDesc desc;
    desc.byteSize = size;
    desc.debugName = name;
    desc.structStride = has(usage, "structured") ? stride : 0;
    desc.format = format_named(format);
    desc.canHaveTypedViews = !format.empty();
    desc.canHaveRawViews = has(usage, "raw");
    desc.canHaveUAVs = has(usage, "uav");
    desc.isVertexBuffer = has(usage, "vertex");
    desc.isIndexBuffer = has(usage, "index");
    desc.isConstantBuffer = has(usage, "constant") || has(usage, "volatile");
    desc.isVolatile = has(usage, "volatile");
    if (desc.isVolatile) desc.maxVersions = 64;  // Vulkan's ring per buffer; NVRHI's validation asks for one
    desc.isDrawIndirectArgs = has(usage, "indirect");
    nvrhi::ResourceStates state = nvrhi::ResourceStates::ShaderResource;
    if (desc.isVertexBuffer || desc.isIndexBuffer)
        state = nvrhi::ResourceStates::VertexBuffer | nvrhi::ResourceStates::IndexBuffer;
    if (desc.isConstantBuffer)  // a pool of vertices, indices and constants (CBufferPool) is read in all three ways
        state = desc.isVertexBuffer || desc.isIndexBuffer ? state | nvrhi::ResourceStates::ConstantBuffer
                                                          : nvrhi::ResourceStates::ConstantBuffer;
    if (has(usage, "upload"))
        desc.cpuAccess = nvrhi::CpuAccessMode::Write;  // in its heap's one state: NVRHI tracks none
    else if (has(usage, "readback"))
        desc.cpuAccess = nvrhi::CpuAccessMode::Read;
    if (!desc.isVolatile && desc.cpuAccess == nvrhi::CpuAccessMode::None)
        desc.enableAutomaticStateTracking(state);  // back in this state after every list
    auto* b = new Buffer();
    b->device = this;
    b->handle = nv->createBuffer(desc);
    b->size = size;
    if (!b->handle) {
        delete b;
        throw Error("the buffer " + name + " was not made");
    }
    return b;
}

Texture* Device::texture(uint32_t width, uint32_t height, const std::string& format, uint32_t depth, uint32_t mips,
                         const std::string& kind, const std::string& usage, const std::string& name) {
    nvrhi::TextureDesc desc;
    desc.width = width;
    desc.height = height;
    desc.mipLevels = mips ? mips : 1;
    desc.format = format_named(format);
    desc.debugName = name;
    desc.isRenderTarget = has(usage, "target") || has(usage, "depth");
    desc.isUAV = has(usage, "uav");
    desc.isTypeless = has(usage, "typeless");
    static const std::map<std::string, nvrhi::TextureDimension> kinds = {
        {"2d", nvrhi::TextureDimension::Texture2D},        {"2darray", nvrhi::TextureDimension::Texture2DArray},
        {"3d", nvrhi::TextureDimension::Texture3D},        {"cube", nvrhi::TextureDimension::TextureCube},
        {"cubearray", nvrhi::TextureDimension::TextureCubeArray}};
    auto it = kinds.find(kind);
    if (it == kinds.end()) throw Error("unknown texture kind " + kind);
    desc.dimension = it->second;
    if (desc.dimension == nvrhi::TextureDimension::Texture3D)
        desc.depth = depth;
    else
        desc.arraySize = depth ? depth : 1;
    nvrhi::ResourceStates state = nvrhi::ResourceStates::ShaderResource;
    if (has(usage, "depth") || has(usage, "target")) {
        desc.useClearValue = true;  // a clear to it is the fast one (the debug layer asks for it)
        desc.clearValue = nvrhi::Color(0.0f);  // 117's far plane; a colour target's transparent black
    }
    desc.enableAutomaticStateTracking(state);
    auto* t = new Texture();
    t->device = this;
    t->handle = nv->createTexture(desc);
    t->desc = desc;
    if (!t->handle) {
        delete t;
        throw Error("the texture " + name + " was not made");
    }
    return t;
}

static D3D12_TEXTURE_ADDRESS_MODE address_of(const std::string& name) {
    if (name == "wrap") return D3D12_TEXTURE_ADDRESS_MODE_WRAP;
    if (name == "mirror") return D3D12_TEXTURE_ADDRESS_MODE_MIRROR;
    if (name == "border") return D3D12_TEXTURE_ADDRESS_MODE_BORDER;
    if (name == "clamp") return D3D12_TEXTURE_ADDRESS_MODE_CLAMP;
    throw Error("unknown address mode " + name);
}

Sampler* Device::sampler(const std::string& filter, const std::string& address, const std::string& compare,
                         float anisotropy, float mip_bias) {
    nvrhi::SamplerDesc desc;
    bool aniso = filter == "aniso";
    desc.minFilter = desc.magFilter = filter != "point" && filter != "point_mip_linear";
    desc.mipFilter = filter != "point";
    desc.maxAnisotropy = aniso ? anisotropy : 1.0f;
    desc.mipBias = mip_bias;
    auto mode = address == "wrap" ? nvrhi::SamplerAddressMode::Wrap
                : address == "mirror" ? nvrhi::SamplerAddressMode::Mirror
                : address == "border" ? nvrhi::SamplerAddressMode::Border
                                      : nvrhi::SamplerAddressMode::Clamp;
    desc.addressU = desc.addressV = desc.addressW = mode;
    desc.reductionType = compare.empty() ? nvrhi::SamplerReductionType::Standard
                                         : nvrhi::SamplerReductionType::Comparison;
    desc.borderColor = nvrhi::Color(0.0f);
    auto address_d3d = address_of(address);  // these raise before anything is allocated
    auto func = compare.empty() ? D3D12_COMPARISON_FUNC_NEVER : D3D12_COMPARISON_FUNC(int(compare_named(compare)));
    auto* s = new Sampler();
    s->device = this;
    s->handle = nv->createSampler(desc);
    if (!s->handle) {
        delete s;
        throw Error("the sampler was not made");
    }
    D3D12_SAMPLER_DESC& x = s->exact;
    D3D12_FILTER base = aniso                        ? D3D12_FILTER_ANISOTROPIC
                        : filter == "point"            ? D3D12_FILTER_MIN_MAG_MIP_POINT
                        : filter == "point_mip_linear" ? D3D12_FILTER_MIN_MAG_POINT_MIP_LINEAR
                                                       : D3D12_FILTER_MIN_MAG_MIP_LINEAR;
    x.Filter = compare.empty() ? base : D3D12_FILTER(int(base) | 0x80);
    x.AddressU = x.AddressV = x.AddressW = address_d3d;
    x.MipLODBias = mip_bias;
    x.MaxAnisotropy = aniso ? UINT(anisotropy) : 1;
    x.ComparisonFunc = func;
    x.MinLOD = 0.0f;
    x.MaxLOD = D3D12_FLOAT32_MAX;
    s->patched = !compare.empty() && compare != "less";  // NVRHI writes LESS for every comparison
    return s;
}

Layout* Device::layout(const std::string& visibility, uint32_t space, const std::vector<LayoutItem>& items) {
    nvrhi::BindingLayoutDesc desc;
    desc.visibility = stage_named(visibility);
    desc.registerSpace = space;
    for (const auto& i : items) {
        nvrhi::BindingLayoutItem item{};
        item.slot = i.slot;
        item.type = kind_named(i.kind);
        item.size = uint16_t(i.count);
        desc.bindings.push_back(item);
    }
    auto* l = new Layout();
    l->device = this;
    l->handle = nv->createBindingLayout(desc);
    if (!l->handle) {
        delete l;
        throw Error("the binding layout was not made");
    }
    return l;
}

Layout* Device::bindless_layout(const std::string& visibility, uint32_t first, uint32_t capacity,
                                const std::vector<std::pair<std::string, uint32_t>>& spaces) {
    nvrhi::BindlessLayoutDesc desc;
    desc.visibility = stage_named(visibility);
    desc.firstSlot = first;
    desc.maxCapacity = capacity;
    for (const auto& [kind, space] : spaces) {
        nvrhi::BindingLayoutItem item{};
        item.slot = space;  // a bindless layout's item names a register space
        item.type = kind_named(kind);
        desc.registerSpaces.push_back(item);
    }
    auto* l = new Layout();
    l->device = this;
    l->bindless = true;
    l->handle = nv->createBindlessLayout(desc);
    if (!l->handle) {
        delete l;
        throw Error("the bindless layout was not made");
    }
    return l;
}

static nvrhi::TextureDimension dimension_named(const std::string& name) {
    if (name.empty()) return nvrhi::TextureDimension::Unknown;
    static const std::map<std::string, nvrhi::TextureDimension> names = {
        {"2d", nvrhi::TextureDimension::Texture2D},       {"2darray", nvrhi::TextureDimension::Texture2DArray},
        {"3d", nvrhi::TextureDimension::Texture3D},       {"cube", nvrhi::TextureDimension::TextureCube},
        {"cubearray", nvrhi::TextureDimension::TextureCubeArray}};
    auto it = names.find(name);
    if (it == names.end()) throw Error("unknown view dimension " + name);
    return it->second;
}

// a released resource's handle is null, and NVRHI writes its view through it: refused here, not a crash in NVRHI
static nvrhi::BindingSetItem item_of(const Binding& b) {
    if ((b.texture && !b.texture->handle) || (b.buffer && !b.buffer->handle) || (b.sampler && !b.sampler->handle))
        throw Error("a released resource in a binding of kind " + b.kind + " at slot " + std::to_string(b.slot));
    nvrhi::ResourceType kind = kind_named(b.kind);
    nvrhi::Format f = format_named(b.format);
    nvrhi::BindingSetItem out = nvrhi::BindingSetItem::None(b.slot);
    switch (kind) {
    case nvrhi::ResourceType::Texture_SRV:
    case nvrhi::ResourceType::Texture_UAV: {
        nvrhi::TextureSubresourceSet sub(b.mip, b.mips ? b.mips : nvrhi::TextureSubresourceSet::AllMipLevels, 0,
                                         nvrhi::TextureSubresourceSet::AllArraySlices);
        if (kind == nvrhi::ResourceType::Texture_UAV) sub.numMipLevels = 1;
        out = kind == nvrhi::ResourceType::Texture_SRV
                  ? nvrhi::BindingSetItem::Texture_SRV(b.slot, b.texture->get(), f, sub, dimension_named(b.dimension))
                  : nvrhi::BindingSetItem::Texture_UAV(b.slot, b.texture->get(), f, sub, dimension_named(b.dimension));
        break;
    }
    case nvrhi::ResourceType::TypedBuffer_SRV: out = nvrhi::BindingSetItem::TypedBuffer_SRV(b.slot, b.buffer ? b.buffer->get() : nullptr, f); break;
    case nvrhi::ResourceType::TypedBuffer_UAV: out = nvrhi::BindingSetItem::TypedBuffer_UAV(b.slot, b.buffer ? b.buffer->get() : nullptr, f); break;
    case nvrhi::ResourceType::StructuredBuffer_SRV: out = nvrhi::BindingSetItem::StructuredBuffer_SRV(b.slot, b.buffer ? b.buffer->get() : nullptr); break;
    case nvrhi::ResourceType::StructuredBuffer_UAV: out = nvrhi::BindingSetItem::StructuredBuffer_UAV(b.slot, b.buffer ? b.buffer->get() : nullptr); break;
    case nvrhi::ResourceType::RawBuffer_SRV: out = nvrhi::BindingSetItem::RawBuffer_SRV(b.slot, b.buffer ? b.buffer->get() : nullptr); break;
    case nvrhi::ResourceType::RawBuffer_UAV: out = nvrhi::BindingSetItem::RawBuffer_UAV(b.slot, b.buffer ? b.buffer->get() : nullptr); break;
    case nvrhi::ResourceType::ConstantBuffer:
    case nvrhi::ResourceType::VolatileConstantBuffer:
        out = nvrhi::BindingSetItem::ConstantBuffer(
            b.slot, b.buffer ? b.buffer->get() : nullptr,
            b.range ? nvrhi::BufferRange(b.offset, b.range) : nvrhi::EntireBuffer);
        out.type = kind;
        break;
    case nvrhi::ResourceType::Sampler: out = nvrhi::BindingSetItem::Sampler(b.slot, b.sampler->get()); break;
    case nvrhi::ResourceType::PushConstants: out = nvrhi::BindingSetItem::PushConstants(b.slot, b.size); break;
    default: throw Error("a binding of kind " + b.kind + " cannot go in a set");
    }
    out.arrayElement = b.element;
    return out;
}

// the place of a register in a layout's table, as NVRHI laid it out (d3d12-resource-bindings.cpp)
static int slot_index(const std::vector<D3D12_DESCRIPTOR_RANGE1>& ranges, D3D12_DESCRIPTOR_RANGE_TYPE type,
                      uint32_t slot) {
    for (const auto& r : ranges)
        if (r.RangeType == type && slot >= r.BaseShaderRegister && slot < r.BaseShaderRegister + r.NumDescriptors)
            return int(r.OffsetInDescriptorsFromTableStart + slot - r.BaseShaderRegister);
    return -1;
}

static D3D12_SRV_DIMENSION srv_dimension(const std::string& name) {
    if (name == "2darray") return D3D12_SRV_DIMENSION_TEXTURE2DARRAY;
    if (name == "3d") return D3D12_SRV_DIMENSION_TEXTURE3D;
    if (name == "cube") return D3D12_SRV_DIMENSION_TEXTURECUBE;
    if (name == "cubearray") return D3D12_SRV_DIMENSION_TEXTURECUBEARRAY;
    return D3D12_SRV_DIMENSION_TEXTURE2D;
}

// What NVRHI cannot write: a null texture view of the shader's dimension (it would crash on a missing texture, and
// fills a missing slot with a buffer's), and a comparison sampler other than LESS (117's depth is reversed)
static void patch_set(Device& device, nvrhi::IBindingSet* set, const std::vector<Binding>& items) {
    auto* s = static_cast<nvrhi::d3d12::BindingSet*>(set);
    auto* layout = s->layout.Get();
    auto* views = device.native->getDescriptorHeap(nvrhi::d3d12::DescriptorHeapType::ShaderResourceView);
    auto* samplers = device.native->getDescriptorHeap(nvrhi::d3d12::DescriptorHeapType::Sampler);
    for (const auto& b : items) {
        if (b.kind == "texture" && !b.texture) {
            int at = slot_index(layout->descriptorRangesSRVetc, D3D12_DESCRIPTOR_RANGE_TYPE_SRV, b.slot + b.element);
            if (at < 0) continue;
            D3D12_SHADER_RESOURCE_VIEW_DESC v{};
            v.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
            v.ViewDimension = srv_dimension(b.dimension);
            v.Shader4ComponentMapping = D3D12_DEFAULT_SHADER_4_COMPONENT_MAPPING;
            v.Texture2D.MipLevels = 1;
            uint32_t index = s->descriptorTableSRVetc + uint32_t(at);
            device.d3d->CreateShaderResourceView(nullptr, &v, views->getCpuHandle(index));
            device.d3d->CopyDescriptorsSimple(1, views->getCpuHandleShaderVisible(index), views->getCpuHandle(index),
                                              D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV);
        } else if (b.kind == "rwtexture" && !b.texture) {
            int at = slot_index(layout->descriptorRangesSRVetc, D3D12_DESCRIPTOR_RANGE_TYPE_UAV, b.slot + b.element);
            if (at < 0) continue;
            D3D12_UNORDERED_ACCESS_VIEW_DESC v{};
            v.Format = DXGI_FORMAT_R32_UINT;
            v.ViewDimension = b.dimension == "2darray" ? D3D12_UAV_DIMENSION_TEXTURE2DARRAY
                              : b.dimension == "3d"    ? D3D12_UAV_DIMENSION_TEXTURE3D
                                                       : D3D12_UAV_DIMENSION_TEXTURE2D;
            uint32_t index = s->descriptorTableSRVetc + uint32_t(at);
            device.d3d->CreateUnorderedAccessView(nullptr, nullptr, &v, views->getCpuHandle(index));
            device.d3d->CopyDescriptorsSimple(1, views->getCpuHandleShaderVisible(index), views->getCpuHandle(index),
                                              D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV);
        } else if (b.kind == "sampler" && b.sampler && b.sampler->patched) {
            int at = slot_index(layout->descriptorRangesSamplers, D3D12_DESCRIPTOR_RANGE_TYPE_SAMPLER, b.slot);
            if (at < 0) continue;
            uint32_t index = s->descriptorTableSamplers + uint32_t(at);
            device.d3d->CreateSampler(&b.sampler->exact, samplers->getCpuHandle(index));
            device.d3d->CopyDescriptorsSimple(1, samplers->getCpuHandleShaderVisible(index),
                                              samplers->getCpuHandle(index), D3D12_DESCRIPTOR_HEAP_TYPE_SAMPLER);
        }
    }
}

// Whether the heaps hold a set of ``layout``: NVRHI writes a set's descriptors through the index its heap hands
// out, an invalid one once the heap is full (the shader-visible sampler heap holds 2048 at most), so a full heap
// is refused here; the range taken to ask is given back at once (no other thread takes descriptors)
static void check_room(Device& device, Layout& layout) {
    auto* laid = static_cast<nvrhi::d3d12::BindingLayout*>(layout.get());
    const std::pair<nvrhi::d3d12::DescriptorHeapType, int> wanted[] = {
        {nvrhi::d3d12::DescriptorHeapType::Sampler, laid->descriptorTableSizeSamplers},
        {nvrhi::d3d12::DescriptorHeapType::ShaderResourceView, laid->descriptorTableSizeSRVetc}};
    for (const auto& [type, count] : wanted) {
        if (count <= 0) continue;
        auto* heap = device.native->getDescriptorHeap(type);
        nvrhi::d3d12::DescriptorIndex at = heap->allocateDescriptors(uint32_t(count));
        if (at == nvrhi::d3d12::c_InvalidDescriptorIndex)
            throw Error(std::string("the ") + (type == nvrhi::d3d12::DescriptorHeapType::Sampler ? "sampler" : "view") +
                        " descriptor heap is full: no room for a binding set");
        heap->releaseDescriptors(at, uint32_t(count));
    }
}

Set* Device::binding_set(Layout& layout, const std::vector<Binding>& items) {
    if (!layout.handle) throw Error("a binding set of a released layout");
    if (layout.bindless) throw Error("a binding set of a bindless layout (it takes a descriptor table)");
    check_room(*this, layout);
    nvrhi::BindingSetDesc desc;
    bool patch = false;
    for (const auto& b : items) {
        bool null = !b.texture && !b.buffer && !b.sampler && b.kind != "push";
        if (null) {  // left out: NVRHI writes a null view of the range's kind, a texture's is patched in
            patch = patch || b.kind == "texture" || b.kind == "rwtexture";
            continue;
        }
        patch = patch || (b.kind == "sampler" && b.sampler && b.sampler->patched);
        desc.bindings.push_back(item_of(b));
    }
    // a static constant buffer as a root view (one slot of it a draw: cbModel's, ``Draw::root_offset``) is a use
    // NVRHI's validation refuses (it asks a volatile one), so such a set is made on the device beneath it
    bool root_view = false;
    for (const auto& b : items)
        root_view = root_view || (b.kind == "volatile" && b.buffer && !b.buffer->get()->getDesc().isVolatile);
    auto* s = new Set();
    s->device = this;
    s->handle = root_view ? native->createBindingSet(desc, layout.get()) : nv->createBindingSet(desc, layout.get());
    if (!s->handle) {
        delete s;
        throw Error("the binding set was not made");
    }
    if (patch) patch_set(*this, s->get(), items);
    return s;
}

Table* Device::descriptor_table(Layout& layout, uint32_t size) {
    if (!layout.handle) throw Error("a descriptor table of a released layout");
    auto* t = new Table();
    t->device = this;
    t->handle = nv->createDescriptorTable(layout.get());
    if (!t->handle) {
        delete t;
        throw Error("the descriptor table was not made");
    }
    nv->resizeDescriptorTable(t->get(), size, false);
    return t;
}

void Device::write_table(Table& table, const Binding& item) {
    if (!table.handle) throw Error("a write to a released descriptor table");
    if (!item.texture && !item.buffer) throw Error("a bindless table takes a texture or a buffer");
    if (!nv->writeDescriptorTable(table.get(), item_of(item))) throw Error("the table refused the descriptor");
}

Shader* Device::shader(const std::string& stage, const std::string& bytecode, const std::string& entry) {
    nvrhi::ShaderDesc desc;
    desc.shaderType = stage_named(stage);
    desc.entryName = entry;
    desc.debugName = entry;
    auto* s = new Shader();
    s->device = this;
    s->handle = nv->createShader(desc, bytecode.data(), bytecode.size());
    if (!s->handle) {
        delete s;
        throw Error("the shader " + entry + " was not made");
    }
    return s;
}

InputLayout* Device::input_layout(const std::vector<std::tuple<std::string, uint32_t, std::string, uint32_t,
                                                               uint32_t, uint32_t, bool>>& elements) {
    std::vector<nvrhi::VertexAttributeDesc> attrs;
    for (const auto& [semantic, index, format, slot, offset, stride, instanced] : elements) {
        nvrhi::VertexAttributeDesc a;
        a.name = semantic;
        a.format = format_named(format);
        a.bufferIndex = slot;
        a.offset = offset;
        a.elementStride = stride;
        a.isInstanced = instanced;
        attrs.push_back(a);
    }
    auto* l = new InputLayout();
    l->device = this;
    l->handle = nv->createInputLayout(attrs.data(), uint32_t(attrs.size()), nullptr);
    if (!l->handle) {
        delete l;
        throw Error("the input layout was not made");
    }
    // NVRHI numbers an attribute's semantic from 0 (its array): the engine's TEXCOORD1 alone needs its own index
    auto* native = static_cast<nvrhi::d3d12::InputLayout*>(l->get());
    for (size_t i = 0; i < elements.size() && i < native->inputElements.size(); i++)
        native->inputElements[i].SemanticIndex = std::get<1>(elements[i]);
    return l;
}

Framebuffer* Device::framebuffer(const std::vector<Texture*>& colors, Texture* depth, uint32_t mip, uint32_t layer) {
    if (colors.empty() && !depth) throw Error("a framebuffer needs a colour or a depth target");
    for (auto* c : colors)
        if (!c || !c->handle) throw Error("a released or missing colour target in a framebuffer");
    if (depth && !depth->handle) throw Error("a released depth target in a framebuffer");
    nvrhi::FramebufferDesc desc;
    nvrhi::TextureSubresourceSet sub(mip, 1, layer, 1);
    for (auto* c : colors) desc.addColorAttachment(nvrhi::FramebufferAttachment().setTexture(c->get()).setSubresources(sub));
    if (depth) desc.setDepthAttachment(nvrhi::FramebufferAttachment().setTexture(depth->get()).setSubresources(sub));
    auto* f = new Framebuffer();
    f->device = this;
    f->handle = nv->createFramebuffer(desc);
    if (!f->handle) {
        delete f;
        throw Error("the framebuffer was not made");
    }
    const auto& first = colors.empty() ? depth->desc : colors[0]->desc;
    f->width = std::max(1u, first.width >> mip);
    f->height = std::max(1u, first.height >> mip);
    return f;
}

Staging* Device::staging(uint32_t width, uint32_t height, const std::string& format) {
    nvrhi::TextureDesc desc;
    desc.width = width;
    desc.height = height;
    desc.format = format_named(format);
    desc.debugName = "readback";
    auto* s = new Staging();
    s->device = this;
    s->handle = nv->createStagingTexture(desc, nvrhi::CpuAccessMode::Read);
    s->desc = desc;
    if (!s->handle) {
        delete s;
        throw Error("the readback texture was not made");
    }
    return s;
}

static nvrhi::BlendFactor factor_named(const std::string& name) {
    using F = nvrhi::BlendFactor;
    static const std::map<std::string, F> names = {
        {"zero", F::Zero},           {"one", F::One},                   {"src_color", F::SrcColor},
        {"inv_src_color", F::InvSrcColor}, {"src_alpha", F::SrcAlpha},  {"inv_src_alpha", F::InvSrcAlpha},
        {"dst_alpha", F::DstAlpha},  {"inv_dst_alpha", F::InvDstAlpha}, {"dst_color", F::DstColor},
        {"inv_dst_color", F::InvDstColor}, {"blend_factor", F::ConstantColor}, {"inv_blend_factor", F::InvConstantColor},
        {"src1_color", F::Src1Color}, {"inv_src1_color", F::InvSrc1Color}, {"src1_alpha", F::Src1Alpha},
        {"inv_src1_alpha", F::InvSrc1Alpha}};  // dual source: the flow map's raster (native-backend.md 16.9)
    auto it = names.find(name);
    if (it == names.end()) throw Error("unknown blend factor " + name);
    return it->second;
}

static nvrhi::StencilOp stencil_op_named(const std::string& name) {
    using S = nvrhi::StencilOp;
    static const std::map<std::string, S> names = {
        {"keep", S::Keep},       {"zero", S::Zero},          {"replace", S::Replace},
        {"incr_sat", S::IncrementAndClamp}, {"decr_sat", S::DecrementAndClamp}, {"invert", S::Invert},
        {"incr", S::IncrementAndWrap},      {"decr", S::DecrementAndWrap}};
    auto it = names.find(name);
    if (it == names.end()) throw Error("unknown stencil operation " + name);
    return it->second;
}

static nvrhi::BlendOp op_named(const std::string& name) {
    if (name == "add") return nvrhi::BlendOp::Add;
    if (name == "subtract") return nvrhi::BlendOp::Subtract;
    if (name == "rev_subtract") return nvrhi::BlendOp::ReverseSubtract;
    if (name == "min") return nvrhi::BlendOp::Min;
    if (name == "max") return nvrhi::BlendOp::Max;
    throw Error("unknown blend operation " + name);
}

static nvrhi::FramebufferInfo targets_of(const PipelineState& state) {
    nvrhi::FramebufferInfo fb;
    for (const auto& c : state.colors) fb.colorFormats.push_back(format_named(c));
    fb.depthFormat = format_named(state.depth);
    fb.sampleCount = state.samples;
    return fb;
}

Pipeline* graphics_pipeline(Device& device, Shader* vs, Shader* hs, Shader* ds, Shader* ps, InputLayout* input,
                            const std::vector<Layout*>& layouts, const PipelineState& state, bool background) {
    nvrhi::GraphicsPipelineDesc desc;
    desc.VS = vs ? vs->get() : nullptr;
    desc.HS = hs ? hs->get() : nullptr;
    desc.DS = ds ? ds->get() : nullptr;
    desc.PS = ps ? ps->get() : nullptr;
    desc.inputLayout = input ? input->get() : nullptr;
    desc.primType = state.topology == "strip"   ? nvrhi::PrimitiveType::TriangleStrip
                    : state.topology == "lines" ? nvrhi::PrimitiveType::LineList
                    : state.topology == "points" ? nvrhi::PrimitiveType::PointList
                    : state.topology.rfind("patch", 0) == 0 ? nvrhi::PrimitiveType::PatchList
                                                 : nvrhi::PrimitiveType::TriangleList;
    if (desc.primType == nvrhi::PrimitiveType::PatchList) {
        const std::string points = state.topology.substr(5);
        int n = points.empty() || points.find_first_not_of("0123456789") != std::string::npos ? 0 : std::stoi(points);
        if (n < 1 || n > 32) throw Error("a patch topology names 1 to 32 control points: " + state.topology);
        desc.patchControlPoints = uint32_t(n);
    }
    if ((desc.HS || desc.DS) && desc.primType != nvrhi::PrimitiveType::PatchList)
        throw Error("a tessellated pipeline draws patches: topology patch<n>");
    for (auto* l : layouts) desc.bindingLayouts.push_back(l->get());
    auto& r = desc.renderState.rasterState;
    r.cullMode = state.cull == "none"    ? nvrhi::RasterCullMode::None
                 : state.cull == "front" ? nvrhi::RasterCullMode::Front
                                         : nvrhi::RasterCullMode::Back;
    r.frontCounterClockwise = state.front_ccw;
    r.depthClipEnable = state.depth_clip;
    r.depthBias = state.depth_bias;
    r.slopeScaledDepthBias = state.slope_bias;
    r.depthBiasClamp = state.bias_clamp;
    r.fillMode = state.wireframe ? nvrhi::RasterFillMode::Wireframe : nvrhi::RasterFillMode::Solid;
    auto& d = desc.renderState.depthStencilState;
    d.depthTestEnable = state.depth_test && !state.depth.empty();
    d.depthWriteEnable = state.depth_write && !state.depth.empty();
    d.depthFunc = compare_named(state.depth_func);
    d.stencilEnable = state.stencil;
    if (state.stencil) {
        nvrhi::DepthStencilState::StencilOpDesc face;
        face.stencilFunc = compare_named(state.stencil_func);
        face.passOp = stencil_op_named(state.stencil_pass);
        face.failOp = stencil_op_named(state.stencil_fail);
        face.depthFailOp = stencil_op_named(state.stencil_depth_fail);
        d.frontFaceStencil = d.backFaceStencil = face;
        d.stencilReadMask = uint8_t(state.stencil_read_mask);
        d.stencilWriteMask = uint8_t(state.stencil_write_mask);
        d.stencilRefValue = uint8_t(state.stencil_ref);
    }
    for (size_t i = 0; i < state.blends.size() && i < nvrhi::c_MaxRenderTargets; i++) {
        const auto& [on, src, dst, op, srca, dsta, opa, mask] = state.blends[i];
        auto& t = desc.renderState.blendState.targets[i];
        t.blendEnable = on;
        t.srcBlend = factor_named(src);
        t.destBlend = factor_named(dst);
        t.blendOp = op_named(op);
        t.srcBlendAlpha = factor_named(srca);
        t.destBlendAlpha = factor_named(dsta);
        t.blendOpAlpha = op_named(opa);
        t.colorWriteMask = nvrhi::ColorMask(mask);
    }
    return make_graphics(device, desc, targets_of(state), background);
}

Pipeline* compute_pipeline(Device& device, Shader& cs, const std::vector<Layout*>& layouts, bool background) {
    nvrhi::ComputePipelineDesc desc;
    desc.CS = cs.get();
    for (auto* l : layouts) desc.bindingLayouts.push_back(l->get());
    return make_compute(device, desc, background);
}

}  // namespace gpu
