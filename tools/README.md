# External tools

The editor runs third-party programs that are not part of the repository. Put them in this folder (any sub-folder
works) or on the `PATH`:

- **texconv** (`texconv.exe`) from https://github.com/microsoft/DirectXTex/releases (MIT). It decodes the games'
  DDS textures for the 3D view.

The environment variable `ANNO_TEXCONV` overrides this search.
