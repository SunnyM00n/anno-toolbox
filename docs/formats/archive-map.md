# The archive map `maindata/file_h.db` (Anno 117, read 2026-11-06 on update 3.0)

The game does not scan the archives of `maindata`. It reads `file_h.db` at start, and that file says which archive
holds each path. The order of the archives on disk plays no part. A path is looked up case-insensitively (lower case,
`\` turned into `/`). The file is a [FileDB](filedb.md) document (version 3) with one root tag, `ArchiveMap`:

| Child | Content |
|---|---|
| `FileMap` | the entries, as pairs: an attribute (id 0x8000, 8 bytes, the 64-bit FNV-1a hash of the path's UTF-16 characters, lower case) followed by an entry tag (id 1) |
| `ArchiveFiles` | the archives, in index order: tag id 1 holding an attribute 0x8000 with the archive path (UTF-16, `maindata/<name>.rda`) and one byte |
| `FileTree`, `LastArchiveFile`, `ResidentBuffers` | empty or a pointer to the browse-pattern archive; not read here |

An entry tag holds these fields:

- `FileName` (UTF-16, no terminator);
- `ArchiveFileIndex` (u32, an index into `ArchiveFiles`; **missing means the first archive**);
- `Position`, `CompressedSize` and `UncompressedSize` (u64, absent for directories);
- `ModificationTime` (u64);
- on some entries, `Flags` (u32): **8 = deleted** (the path stays in the map, the game has no such file) and 1 (341
  entries on the installed build, not traced).

The installed 3.0 build has 452,477 entries and 42 archives (two language packs, French and Chinese, are not installed
here). 22,836 entries are flagged deleted. 32,044 paths are shipped by two or more archives. For 31,984 of them, the
rule "the later archive name wins" picks the map's archive. The other 60 (textures and interface pictures) are flagged
deleted by patch 300: the game has no such file, while the name rule would read an older patch's copy.

`sources.anno117.read_archive_map` returns path -> (archive name, flags). `Anno117Files` applies it over the name
rule:

- the map's archive wins;
- a deleted path, or a path in an archive that is not installed, is no file;
- a path the map does not list keeps the name rule's archive.

The read takes about 4 seconds on the installed build (148 MB).
