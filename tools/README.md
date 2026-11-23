# External tools

The editor runs third-party programs that are not part of the repository. Put them in this folder (any sub-folder
works) or on the `PATH`:

- **texconv** (`texconv.exe`) from https://github.com/microsoft/DirectXTex/releases (MIT). It decodes the games'
  DDS textures for the 3D view.

- **packed_codebooks_aoTuV_603.bin**, the Vorbis codebook library of ww2ogg
  (https://github.com/hcs64/ww2ogg, BSD-3-Clause). It lets the Sound app play and export Wwise Vorbis sounds. The
  installed Toolbox ships it here with its licence notice (`ww2ogg-COPYING.txt`); in a checkout, put it in this folder.

The environment variables `ANNO_TEXCONV` and `ANNO_WW_CODEBOOKS` override this search.
