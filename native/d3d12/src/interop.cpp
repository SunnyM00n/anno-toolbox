// QRhi's own resources reached from here: a QRhiTexture's or QRhiBuffer's ID3D12Resource (PySide6 does not expose
// nativeTexture() and nativeBuffer(): they are called through the object's virtual table, laid out as Qt 6's public
// qrhi.h declares it), wrapped as NVRHI resources for a binding set, and the transitions a draw on QRhi's list needs.
#include "gpu.h"

namespace gpu {

namespace {

// QRhiResource's and QRhiTexture's / QRhiBuffer's virtual functions in qrhi.h's order (Qt 6.6 to 6.12); only the
// layout matters: the objects are QRhi's, never made here
struct NativeTexture {
    uint64_t object;
    int layout;
};
struct NativeBuffer {
    const void* objects[3];
    int slot_count;
};

struct QRhiTextureLike {
    virtual void deleting_destructor() = 0;  // ~QRhiResource: one slot on MSVC
    virtual int resource_type() const = 0;
    virtual void destroy() = 0;
    virtual bool create() = 0;
    virtual NativeTexture native_texture() = 0;
    virtual bool create_from(NativeTexture src) = 0;  // the slot after nativeTexture()
};

struct QRhiBufferLike {
    virtual void deleting_destructor() = 0;
    virtual int resource_type() const = 0;
    virtual void destroy() = 0;
    virtual bool create() = 0;
    virtual NativeBuffer native_buffer() = 0;
};

constexpr int QRHI_BUFFER = 0, QRHI_TEXTURE = 1;  // QRhiResource::Type

ID3D12Resource* checked(const void* object) {
    if (!object) return nullptr;
    auto* unknown = static_cast<IUnknown*>(const_cast<void*>(object));
    ComPtr<ID3D12Resource> resource;
    if (FAILED(unknown->QueryInterface(IID_PPV_ARGS(&resource)))) return nullptr;
    return resource.Get();  // QRhi holds it: no reference kept here
}

}  // namespace

std::pair<uintptr_t, int> qrhi_texture(uintptr_t texture) {
    auto* t = reinterpret_cast<QRhiTextureLike*>(texture);
    if (!t || t->resource_type() != QRHI_TEXTURE) throw Error("not a QRhiTexture");
    NativeTexture n = t->native_texture();
    ID3D12Resource* r = checked(reinterpret_cast<const void*>(n.object));
    if (!r) throw Error("the QRhiTexture holds no Direct3D 12 resource (not a Direct3D 12 QRhi?)");
    return {uintptr_t(r), n.layout};
}

bool qrhi_create_from(uintptr_t texture, uintptr_t resource, int state) {
    auto* t = reinterpret_cast<QRhiTextureLike*>(texture);
    if (!t || t->resource_type() != QRHI_TEXTURE) throw Error("not a QRhiTexture");
    if (!checked(reinterpret_cast<const void*>(resource))) throw Error("not a Direct3D 12 resource");
    // QRhi's Direct3D 12 backend keeps the resource without owning it: the module releases it, after QRhi's wrapper
    return t->create_from(NativeTexture{uint64_t(resource), state});
}

uintptr_t Texture::native() const {
    return uintptr_t(get()->getNativeObject(nvrhi::ObjectTypes::D3D12_Resource).pointer);
}

uintptr_t qrhi_buffer(uintptr_t buffer, int slot) {
    auto* b = reinterpret_cast<QRhiBufferLike*>(buffer);
    if (!b || b->resource_type() != QRHI_BUFFER) throw Error("not a QRhiBuffer");
    NativeBuffer n = b->native_buffer();
    if (n.slot_count <= 0) throw Error("the QRhiBuffer has no native buffer");
    const void* at = n.objects[slot % n.slot_count];
    ID3D12Resource* r = checked(at);
    if (!r && at) r = checked(*static_cast<void* const*>(at));  // a pointer to the resource's pointer
    if (!r) throw Error("the QRhiBuffer holds no Direct3D 12 resource");
    return uintptr_t(r);
}

Texture* Device::wrap_texture(uintptr_t resource, const std::string& format, const std::string& kind,
                              const std::string& name) {
    auto* r = reinterpret_cast<ID3D12Resource*>(resource);
    D3D12_RESOURCE_DESC rd = r->GetDesc();
    nvrhi::TextureDesc desc;
    desc.width = uint32_t(rd.Width);
    desc.height = rd.Height;
    desc.mipLevels = rd.MipLevels;
    desc.format = format_named(format);
    desc.debugName = name;
    desc.isRenderTarget = (rd.Flags & (D3D12_RESOURCE_FLAG_ALLOW_RENDER_TARGET |
                                       D3D12_RESOURCE_FLAG_ALLOW_DEPTH_STENCIL)) != 0;
    desc.isUAV = (rd.Flags & D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS) != 0;
    if (rd.Dimension == D3D12_RESOURCE_DIMENSION_TEXTURE3D) {
        desc.dimension = nvrhi::TextureDimension::Texture3D;
        desc.depth = rd.DepthOrArraySize;
    } else {
        desc.arraySize = rd.DepthOrArraySize;
        desc.dimension = kind == "cube"   ? nvrhi::TextureDimension::TextureCube
                         : kind == "2darray" || rd.DepthOrArraySize > 1 ? nvrhi::TextureDimension::Texture2DArray
                                                                     : nvrhi::TextureDimension::Texture2D;
    }
    auto* t = new Texture();
    t->device = this;
    t->handle = nv->createHandleForNativeTexture(nvrhi::ObjectTypes::D3D12_Resource, nvrhi::Object(r), desc);
    t->desc = desc;
    if (!t->handle) {
        delete t;
        throw Error("QRhi's texture " + name + " was not wrapped");
    }
    return t;
}

Buffer* Device::wrap_buffer(uintptr_t resource, uint32_t stride, const std::string& usage, const std::string& name) {
    auto* r = reinterpret_cast<ID3D12Resource*>(resource);
    D3D12_RESOURCE_DESC rd = r->GetDesc();
    nvrhi::BufferDesc desc;
    desc.byteSize = rd.Width;
    desc.structStride = stride;
    desc.debugName = name;
    desc.canHaveUAVs = (rd.Flags & D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS) != 0;
    desc.canHaveRawViews = usage.find("raw") != std::string::npos;
    desc.isConstantBuffer = usage.find("constant") != std::string::npos;
    auto* b = new Buffer();
    b->device = this;
    b->handle = nv->createHandleForNativeBuffer(nvrhi::ObjectTypes::D3D12_Resource, nvrhi::Object(r), desc);
    b->size = desc.byteSize;
    if (!b->handle) {
        delete b;
        throw Error("QRhi's buffer " + name + " was not wrapped");
    }
    return b;
}

void transitions(uintptr_t list_ptr, const std::vector<std::tuple<uintptr_t, int, int>>& items) {
    auto* list = reinterpret_cast<ID3D12GraphicsCommandList*>(list_ptr);
    std::vector<D3D12_RESOURCE_BARRIER> out;
    for (const auto& [resource, before, after] : items) {
        if (before == after) continue;
        D3D12_RESOURCE_BARRIER b{};
        b.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
        b.Transition.pResource = reinterpret_cast<ID3D12Resource*>(resource);
        b.Transition.Subresource = D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES;
        b.Transition.StateBefore = D3D12_RESOURCE_STATES(before);
        b.Transition.StateAfter = D3D12_RESOURCE_STATES(after);
        out.push_back(b);
    }
    if (!out.empty()) list->ResourceBarrier(UINT(out.size()), out.data());
}

}  // namespace gpu
