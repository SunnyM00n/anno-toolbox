"""The suite's 3D drawing, shared by the island editor and the viewer: what the views need to draw an Anno model or
island as the games do, on one of two devices. docs/tool-suite.md sections 19 ("Layers") and 21 (the renderer).

``shaders`` (our GLSL), ``light`` (the looks) with ``gamelook`` (117's sky light, sun and grade), ``viewcolors``;
``camera``, ``picking``, ``cull``, ``loader``; ``modellibrary`` (a config's parts for instancing); ``particlefx``,
``particledraw`` (particle effects). The devices: moderngl on OpenGL (``glformat``), or Qt's QRhi on Direct3D 12, 11, Vulkan or
OpenGL with ``rhidevice`` (the same calls, recorded by ``rhipass``; sRGB levels by ``srgbmips``) in ``rhiview``'s
widget, our GLSL ported (``glslport``) and baked (``shaderbake``). The games' own shaders on QRhi share
``gameshader`` (bindings, inputs by name) with ``rawbuffers``, ``gamedraw`` (a mesh, the pipeline's parts),
``nativepass`` (a pass of draws, a frame's composite); 117's: ``gamebind`` (what a shader binds), ``gamecompute``
(compute chains), ``gameframe`` (the frame), ``gamelight``, ``gameshadow`` with ``shadowfit``, ``lightclusters``,
``gamesky``, ``gamepost`` with ``eyeadapt``, ``gamewater``, ``gameground``, ``gameatmosphere``, ``gameclouds``,
``gameocean``, ``gamesea``, ``gameinland``, ``gameextras`` (those handed to the island view's frame), ``gamedecals``
(the config decals), ``terraindraw`` with ``terrainnodes``, ``terrainpages`` and ``terrainbake`` (an island's
terrain); 2070's: ``fxcdraw`` (an FXC draw), ``frame2070`` with ``sky2070``, ``post2070``, ``ocean2070`` (its sea),
``coast2070``; 1404's, 1701's: ``frame1404``, ``frame1701`` with ``sky1701``, ``ocean1404``, ``ocean1701``,
``coast1701``; 2205's, 1800's: ``frameforward`` with ``ambientforward``, ``shadowforward``, ``lightsforward``, ``particleforward``,
``postforward``, ``skyforward``, ``decalforward``, ``clothforward``, ``eyeforward``, ``ssaoforward``, ``ssrforward``,
``waves1800``, ``sea2205``, ``ocean1800``, ``oceanfft1800`` (seas), ``inland1800`` (rivers), ``objects2205``,
``mipchain``, ``blocktexture`` (a map's levels, as stored); ``nativelook``; ``clothsim``, ``clothcollide``, ``clothmesh``.
"""
