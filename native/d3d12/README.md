# The render module (`_nativegpu`)

A small C++ extension that gives the island view's game look Direct3D 12 as the game itself uses it: the game's
shipped compiled shaders (DXIL) bound as they are, with root signatures built from their own bindings. It is
[NVRHI](https://github.com/NVIDIA-RTX/NVRHI) underneath and a narrow [nanobind](https://github.com/wjakob/nanobind)
interface on top. Python keeps the scene logic (what to draw, the constants, the phases); the module makes the
resources and records the commands. The design and the steps: `docs/rendering/native-backend.md`.

The Toolbox works without it: without the module, or on a machine without Direct3D 12, the island view draws its own
look. The installer carries the module; a source checkout builds it with `scripts/build_native.py`.

## What is in it

| File | What |
|---|---|
| `src/gpu.h` | the layer's types: the device, one wrapper per resource, the pipeline state, a draw's arguments |
| `src/device.cpp` | the device (see below the table), NVRHI's and the debug layer's messages, the shader model and binding tier it offers, the process's use of the adapter's own memory with its budget (`video_memory`, for `scripts/native_compare.py`), and the names Python passes (NVRHI's format names, binding kinds, comparisons) |
| `src/resources.cpp` | buffers, textures, samplers, binding layouts (by register space, bindless ranges), binding sets (a constant buffer's piece by offset and size), descriptor tables, shaders from DXIL, input layouts, and the descriptions of graphics and compute pipelines (see below the table) |
| `src/pipelines.cpp` | the pipelines made: on the module's worker threads when asked (`background`), and loaded from or stored into the device's pipeline library, whose bytes Python keeps on disk (see below the table) |
| `src/commands.cpp` | command lists, and the work recorded into a command list someone else owns (QRhi's) (see below the table) |
| `src/interop.cpp` | QRhi's own resources: a `QRhiTexture`'s or `QRhiBuffer`'s Direct3D 12 resource and the state QRhi tracks it in, wrapped for binding sets, and the transitions a draw on QRhi's list needs; the other way, a module texture handed to a `QRhiTexture` (`qrhi_create_from`) |
| `src/module.cpp` | the Python interface |
| `tests/layer_test.cpp` | checks that need no GPU: the names, and on the software rasteriser the resources, the patched descriptors and a clear read back |
| `deps.json` | the pinned sources and the module's version |
| `THIRD-PARTY.md` | the licences of NVRHI, the DirectX headers and nanobind |

`src/device.cpp` makes the device here (a device of its own, or Windows' software rasteriser for the tests), or takes it
over from QRhi.

The graphics pipelines of `src/resources.cpp` carry the game's stencil test: both faces' compare and operations, masks
and reference. They also carry dual-source blend factors.

A binding set, a descriptor table and a write into one refuse a released resource, sampler, layout or table: they
raise. NVRHI would write the view through the released object's null handle and crash the process. A binding set
also raises when its descriptor heap is full. The shader-visible sampler heap holds 2048 descriptors at most. NVRHI
writes a set past that through an invalid index, and the driver crashes the process.

`src/commands.cpp` holds these:
- command lists: uploads, clears, copies of a whole texture or a rectangle of a layer and level, draws, dispatches,
  timers, and transitions of a texture someone else left in another state. An open list with nothing recorded since
  its last submit is not submitted again (`execute`: most passes leave theirs empty);
- the work recorded into a command list someone else owns (QRhi's):
  - one draw (`record_external`);
  - many draws made once (`SDrawItem`, `record_draws`), each with its instance count and root offset a frame, and with
    what the draw before set not set again. A pipeline with a stencil test sets its reference. A kept draw holds its
    pipeline, buffers and sets alive. A released draw, an offset past a buffer's end, or push constants larger than the
    root signature's raise instead of being recorded;
  - many indexed draws made once whose instance counts the GPU wrote (`record_indirect`): each one an
    `ExecuteIndirect` of one `DrawIndexedInstanced` from its five words of an `indirect` buffer, which the caller has
    moved into the indirect argument state (the shadow casters' culling, `render/shadowcullnative.py`). The device
    keeps the one command signature. Arguments past the buffer's end, a buffer made without `indirect` and a draw
    without indices raise;
  - a compute dispatch (`record_dispatch`, inside a compute pass of QRhi's);
- a barrier between unordered accesses (`uav_barrier`, also on the module's own lists, where NVRHI places none between
  dispatches of an unchanged set);
- a copy between buffers (`copy_buffer`, refused past either buffer's end);
- and a copy of a whole texture into one of the same size and format (`copy_texture`, refused between unlike ones).

Draws and dispatches bind their root parameters through one routine (`bind_root`).

`src/pipelines.cpp` makes every pipeline as the game makes its own:
- A pipeline asked for with `background=True` is made by one of the module's worker threads (a quarter of the
  processor's threads, 1 to 4). Its `ready` turns true once it is in. A draw recorded with one still being made is
  left out (`record_draws`), and a draw on the module's own lists waits for it.
- Every pipeline is first looked up in the device's pipeline library (`ID3D12PipelineLibrary`), by a 128-bit name of
  its shaders, input elements, fixed state, targets and binding layouts. One not there is made by the driver and
  stored. `pipeline_library()` gives the library's bytes behind a header naming the adapter, the driver's version,
  the module's version and NVRHI's commit, and `open_pipeline_library(bytes)` takes them back on a later start.
  Bytes of another adapter, driver or module are refused (Direct3D refuses another driver's itself), and the library
  starts empty.
- The workers touch Direct3D 12's own objects only. NVRHI's root signature cache and the pipeline's NVRHI handle are
  made on the caller's thread: the root signatures are kept per set of binding layouts by the module, and a done
  worker's pipeline is wrapped when `ready` is next asked.

Three places reach past NVRHI's public interface, each to NVRHI's own Direct3D 12 types (`nvrhi/src/d3d12`,
pinned with the commit):
- the comparison samplers (NVRHI writes `LESS` only; 117's depth is reversed, so the descriptors are written again
  with the engine's comparison);
- the null texture views of a shader's dimension (NVRHI writes a buffer's);
- an input element's semantic index (NVRHI counts from 0 per attribute; the engine's meshes name `TEXCOORD1` alone).

Recording into QRhi's list reads the root signature and tables of NVRHI's pipeline and sets. `CommandList.transitions`
records barriers on NVRHI's open list ahead of NVRHI's own (its permanent state assumed for a texture QRhi read
since). A volatile constant buffer gets a version count only NVRHI's validation asks for.

A root constant buffer view of a static buffer is a use NVRHI's validation refuses. An example is the objects'
`cbModel`: one buffer of every draw's slot, with a draw's `root_offset` added to its address. So a binding set holding
one is made on the device beneath the validation layer.

One place reaches past PySide6. `QRhiTexture::nativeTexture()`, `QRhiTexture::createFrom()` (the slot after it) and
`QRhiBuffer::nativeBuffer()` are not exposed to Python, so `interop.cpp` calls them through the object's virtual
table, laid out as Qt 6's public `qrhi.h` declares it (the same from Qt 6.6 to 6.12).

QRhi's Direct3D 12 backend keeps a resource it was given by `createFrom` without owning it. So the module releases it,
after QRhi's wrapper. `setNativeLayout` (exposed) tells QRhi the state the module left it in. The module first asks the
object's `resourceType()` and refuses anything but a texture or a buffer. It checks the result with `QueryInterface`
for `ID3D12Resource`. A Qt update that reorders those virtual functions breaks it, and the tests say so
(`tests/render/test_terrainnative.py`).

The wrapped resources never go into a command list of NVRHI's. NVRHI tracks no state of theirs, so a draw on QRhi's
list moves them into shader reads and back to the state QRhi tracks. Work that writes on QRhi's list (the shading, the
haze, the cascades' fit, the post chain and the composite into the view's own target: `render/nativeforeign.py`) moves
every resource it touches by hand and puts it back. A QRhi buffer's state is not readable, so a read-back or an upload
in the pass's opening batch makes QRhi move it into a known one first.

Every command list is made with NVRHI's `enableImmediateExecution` off. A scene keeps its own list open between its
submits, and NVRHI's validation allows only one open immediate list.

Note: the `qrhi.h` layout was checked against 6.12.0's header on 2026-11-17. The probe of QRhi's `createFrom` backend
(no reference taken or dropped) was made on 2026-11-18. The one open immediate list was seen on 2026-11-17, with the
debug layer.

## Build

```bash
pip install nanobind==2.9.2        # once, in the Python the Toolbox runs with
python scripts/build_native.py     # into src/<package>/render/_nativegpu.pyd
python scripts/build_native.py --test   # and the layer's test program
```

Needs Windows, Visual Studio's C++ build tools (2022 or later; the script finds them with `vswhere` and sets up the
compiler itself), CMake and Ninja (both come with the build tools) and git. The script fetches NVRHI and the DirectX
headers by commit into `build/native/deps/` once, configures `build/native/cp3XX/` and copies the module into the
package. It is built for Python's stable ABI, so one `.pyd` serves Python 3.12 and later. The C++ runtime is linked
in, so nothing has to be installed beside it. The module is about 1 MB.

Note: the module was built on Python 3.14 and loaded by 3.15 on 2026-11-17.

`scripts/build_app.py` builds it when it is missing or older than this folder, and checks that the program folder
carries it (`--no-native` leaves it out). The public edition's CI builds it on the Windows runner and runs its
tests; the release workflow builds it before the program.

## Debugging

`ANNO_D3D12_DEBUG=1` turns the Direct3D 12 debug layer on for a device the module makes itself. The debug layer is
Windows' optional "Graphics Tools" feature, and without it the switch does nothing. A device taken over from QRhi has
the layer when QRhi made it with one. `Device.messages()` returns NVRHI's and the debug layer's messages since the
last call. `ANNO_NATIVE_GPU=0` keeps the module from loading at all.
