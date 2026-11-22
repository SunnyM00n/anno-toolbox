# RDA archives (the format `rda.py` reads and writes)

Every Anno since 1404 keeps its data in `.rda` archives, in one of three versions: "Resource File V2.0" (1404, 2070),
"Resource File V2.2" (the History Editions' own archives, 2205, 1800, 117) and "Crypted Resource File V1.1" (1701).
V2.0 and V2.2 share one layout. They differ only in integer width, magic encoding and the encryption seed. All
integers are little endian.

The references are RDAExplorer (`ext-tools/RDAExplorer`) and the AnnoRDA library by Lysann Schlegel (MIT).

Every statement below about the games was checked against the installed archives (read only). What the installed games
do not contain is marked.

Note: checked on 2026-11-07.

## The variants

| Version | Magic | Integers | Found in (installed builds) | Block flags found, with the number of blocks |
|---|---|---|---|---|
| V1.1 | `Crypted Resource File V1.1`, encrypted | 32 bit | 1701: `data0..5.rda`, `data2_<lang>`, `data3_<lang>` (8 archives) | no blocks; entry flags 7 (zlib) in 4 archives, 6 (stored) in 4 |
| V2.0 | `Resource File V2.0` as UTF-16LE (36 bytes) | 32 bit | 1404: `maindata`, `addon` (17 archives); 2070: `maindata` (106 archives) | 1404: 0 (43), 2 (5), 3 (43), 7 (4); 2070: 0 (377), 2 (9), 3 (649), 7 (36), 8 (92) |
| V2.2 | `Resource File V2.2` as UTF-8 (18 bytes) | 64 bit | 1404 `ahe/a4he0.rda` and 1701 `ahe/a3he0.rda` (flags 0 only); 2205 (39 archives); 1800 (36); 117 (40) | 2205: 0 (714), 1 (3), 2 (7), 4 (15), 8 (62,222); 1800: 0 (5,161), 4 (4), 8 (606,981); 117: 0 (772), 8 (23,247) |

Two files of 2070's `maindata`, `checksums.rda` and `addon_checksums.rda`, are not archives. They are text lists of
`<32 hex digits> maindata/<name>.rda` lines. The game reads them (engine-notes "Anno 2070 archives").

The digest is not the MD5 of the installed archives, which patches replaced, and was not traced further. No archive
carries a checksum or digest of its own: every field of the three headers below is accounted for.

The block flags found in the wild, by value (the bits are the next section):

| Flags | Meaning | Found in |
|---|---|---|
| 0 | stored: file data and file headers plain | every game |
| 1 | compressed (zlib) | 2205 only (`data5`, the shader cache) |
| 2 | encrypted, not compressed (sizes equal) | 1404, 2070, 2205 (`data1`: 427 XML files) |
| 3 | compressed, then encrypted | 1404, 2070 (every `patch9.rda` block) |
| 4 | resident, stored | 1800 (`data8`), 2205 (`data13`) |
| 7 | resident, compressed, encrypted | 1404, 2070 (`addon0`: model configs) |
| 8 | deleted | 2070, 1800, 2205, 117 |

No installed archive has V2.2 with flags 3 or 7, V2.0 with flags 1 or 4, flags 5 or 6, or a resident block that is
also deleted. `rda.py` reads and writes all of them (tests build each on synthetic data). Only the combinations above
are confirmed on the games' own files.

## Layout

```
header:
    magic            V2.0: "Resource File V2.0" as UTF-16LE (36 bytes)
                     V2.2: "Resource File V2.2" as UTF-8   (18 bytes)
    padding          V2.0: 1008 bytes, V2.2: 766 bytes (zero in every archive checked)
                     V2.0: the magic sits in a 520-byte field; a uint32 signature length at 0x208 and up
                     to 516 signature bytes at 0x20C follow (an optional archive signature; zero in every
                     1404 and 2070 archive checked)
    uint             offset of the first block header (byte 1044 in V2.0, 784 in V2.2); the first file's
                     data starts right behind it (1048 / 792)
block header (at offset O; uint = uint32 in V2.0, uint64 in V2.2):
    uint32 flags     1 = compressed, 2 = encrypted, 4 = resident (contiguous data), 8 = deleted
    uint32 num_files
    uint   compressed_headers_size
    uint   uncompressed_headers_size
    uint   next_block_offset   the last block points at the end of the file
```

The blocks follow each other in file order in every archive checked, the last block header ends the file, and the
chain never loops. A block can hold no files (1404 has such blocks with flags 0, 2 and 3; 2205 ships archives with no
files at all). Readers skip it.

The file headers sit *before* their block header. They start at `O - compressed_headers_size`. After decryption /
decompression they are `num_files` records of 520 + 5 × sizeof(uint) bytes:

```
    520 bytes UTF-16LE path (NUL padded)
    uint data_offset
    uint compressed_size
    uint uncompressed_size
    uint modification_timestamp   unix seconds (UTC); 0 in deleted entries and in the two History Edition archives
    uint (unused)                 0 in every entry of every archive checked
```

With flag 4 (the game calls it memory-resident), the block's file data is one blob directly before the file headers.
The two uints right before the block header hold the blob's compressed and uncompressed size. So the file headers
start at `O - 2 × sizeof(uint) - compressed_headers_size`, the blob starts at the file headers' start minus the
blob's stored size, and `data_offset` is relative to the decoded blob.

A resident entry's `compressed_size` equals its `uncompressed_size` even when the blob is compressed (the blob is the
unit). Without flag 4, `data_offset` is absolute and every file is decrypted / decompressed on its own. Decryption
comes before inflation, as in the game.

Flags 1 and 2 apply to the file headers as well as to the data. There is no archive with a compressed directory and
stored data.

A deleted block (flag 8, found alone, never with other bits) lists paths the archive does not have. Its directory is
stored (sizes equal), every entry has offset, sizes and timestamp 0, and there is no data.

2070 groups many paths in a block (92 blocks, 3,104 paths in 17 archives, one block with 370). 1800, 2205 and 117 write
one block per path (2205 repeats some paths: 62,222 blocks name 30,044 paths). No path is both live and deleted within
one archive.

The game keeps the paths of a deleted block as "no such file". 117's archive map (`formats/archive-map.md`) flags
them deleted, and a path that an older archive still holds is not found. The map has 22,836 entries on the installed
3.0 build, against 23,247 deleted blocks in the five archives that have them. The difference is not traced.

`CRdaArchive.deleted` returns these paths, and `entries` lacks them. Applying them to older archives is the caller's
job: `sources.anno117` does it through the archive map, and `overlay.CSourceTree` for 2070 does not yet. No 2070
archive was checked for a deleted path that an earlier archive holds.

## Nested archives

`.a6m`, `.a7m` and `.a7t` files and `rd3d.data` hold V2.2 archives themselves (117's islands, 1800's islands and
textures). `CRdaArchive.open_member(path)` reads one in memory, and `CRdaArchive.from_bytes` opens any archive held in
memory.

## Version 1.1 (Anno 1701)

Anno 1701's archives (`data0..5.rda` and the language packs) are "Crypted Resource File V1.1": one directory,
no blocks.

```
header (260 bytes, one encrypted stream):
    magic            "Crypted Resource File V1.1", NUL padded to 256 bytes
    uint32 count
directory (count x 276 bytes, a second encrypted stream, directly after the header):
    256 bytes ASCII path (NUL padded; lower case with forward slashes in all 1701 archives)
    uint32 data_offset          absolute; the first file starts right behind the directory
    uint32 compressed_size
    uint32 uncompressed_size
    uint32 flags                bit 1 = zlib; the shipped files have 7 (zlib) or 6 (stored)
    uint32 modification_timestamp   unix seconds
file data, never encrypted
4 trailing bytes (zero)
```

Both streams use the V2.0 seed. The History Edition's own archive (`ahe/a3he0.rda`) is V2.2. `rda.write_v11` and
`rda.write_v11_file` write this layout.

## Encryption and compression

Encryption is a 16-bit XOR stream from an LCG (`x = 214013 * x + 2531011 mod 2**32`, key = `(x >> 16) & 0x7FFF`).
It is seeded with `0xA2C2A` (V2.0 and V1.1: 1404, 1701, 2070) or `0x71C71C71` (V2.2: 2205) and restarted for every
stream (each directory, each file, each resident blob, the V1.1 header and directory). A trailing odd byte is left
as it is (AnnoRDA). 1800 and 117 encrypt nothing, so the V2.2 seed is confirmed by 2205's encrypted blocks only.
Compression is zlib (the games write level 1: header `78 01`).

## Override order

Anno 2070 loads these archives, in this order:

1. `maindata/data0..5.rda`;
2. the language packs `<lang>0..8.rda`;
3. `patch1..8.rda`;
4. `exedata.rda`;
5. `addon0..1.rda` and `addon_<lang>0..1.rda`;
6. last, `patch9.rda`.

A file in a later archive replaces the same path from an earlier one. `patch1..8` are mounted only when
`checksums.rda` lists them, and `patch9` unconditionally (engine-notes "Anno 2070 archives"). `overlay.CSourceTree`
reproduces that order (`README.md`, "Reading 2070"). `tests/formats/test_rda.py` checks the reader against archives written by
a small test writer.

Where the shipped archives overlap across these groups, the order picks the newer copy. The 15 `guidata` UI files come
from `exedata` (2012) over `patch3` (2011). The two credits texts come from `patch9` (2022) over `eng2` and
`addon_eng1`.

Anno 1701 has no fixed override order. It mounts `data<n>.rda` (else `data<n>_<lang>.rda`) for n = 0, 1, ... up to the
first gap. Of two entries for one path, it keeps the one with the newer timestamp (a tie keeps the earlier archive).
`isd1701.CTree1701` reproduces it (`formats/anno1701-world-format.md`, "Archives").

## Reading

`rda.CRdaArchive(path, cache_dir)` (or `CRdaArchive.from_bytes(data)`) detects the version from the magic, parses the
index lazily and caches it (`<name>.index.json`, with the deleted paths). It offers:

- `entries` (path to `SRdaEntry`) and `deleted`;
- `read` / `read_entry` and `open_member`;
- `extract` (whole-or-nothing, through a temp name);
- `blocks()` (every block header with its flags and sizes, for tools).

A later block overrides an earlier one for the same path, and a later deleted block removes it.

Every archive of the six installed games reads. `tests/formats/test_rda_variants.py` indexes all of them and reads the
smallest two members of every flags kind (`ANNO1404_DIR`, `ANNO1701_DIR`, `ANNO2070_DIR`, `ANNO1800_DIR`,
`ANNO2205_DIR`, `ANNO117_DIR`; each skips without its variable).

## Writing

The writers:

- `rda.write_v22` writes 117's island `.a7m` layout (one block per file, flag 1, zlib level 1).
- `rda.write_v22_stored` writes the same with nothing compressed or encrypted (flags 0), as 117's savegames are
  (`formats/save-format.md`).
- `rda.write_v20` writes 2070's `patch9.rda` layout (one block per file, flags 3: compressed, then encrypted with
  the V2.0 key stream).
- `rda.write_v11` writes the 1701 layout (flags 7, or 6 with `compress=False`).

`write_v20` and `write_v22` take `flags=` for any mix of compressed and encrypted (stored plus encrypted is 1404's
and 2205's block kind). All read back through `CRdaArchive` (`tests/formats/test_rda.py`, `tests/formats/test_rda_variants.py`).

`rda.CRdaWriter` writes the archives the game mounts:

- It writes to disk member by member (under `<name>.part`, moved into place by `close`).
- A block holds many members (at most 10,000, so a block's directory stays a few MB).
- A block takes any mix of flags 1 and 2 (stored, zlib, XOR, zlib + XOR) and a level per member (0 is stored
  deflate: it inflates as a copy).
- Members of equal content, flags and level share one stored copy. The directory entry is only an offset and a size;
  `CRdaArchive.read_entry` and the game read each entry as given.
- `add_deleted(paths)` writes a deleted block.
- `allow_resident=True` also allows flag 4: a block's members become one blob (flags 4, 5, 6, 7; built in memory, so
  a block is cut at 64 MiB of members) with the layout above. Without it flag 4 is refused, because 117's game
  refuses resident members (read from its code on 3.0).
- A V2.0 archive stops with an error where an offset would pass 4 GiB (1404's largest archive is 1.4 GB).

`rda.archive_path` refuses what the game cannot find (read from its code on 3.0):

- a path with a capital letter or a backslash;
- a leading `./` or `/`;
- blanks at an end;
- a path over 259 characters.

Only the looked-up path is normalised, never the stored one. An empty member is refused too (a size of 0 is never
found). The directory sits right before its block header, and the last block points at the end of the file
(`engine-modloader.md` "A mod's archives through the helper library").

`rda.write_v11_file` writes V1.1 to disk the same whole-or-nothing way (the directory comes first, so it takes the
members as a list). The round trips through `CRdaArchive` are in `tests/formats/test_rda.py` and
`tests/formats/test_rda_variants.py`.

Note: `rda.CRdaWriter` was added on 2026-11-07.

### Writing an archive again as it was

`formats/rdalayout.py` keeps what a reader drops and writes the archive back (docs/mod-import.md, section 16). It
offers three functions:

- `read_layout` (version, blocks with flags and counts, member order, times, zlib level, shared copies; deleted and
  empty blocks included);
- `pack(layout, members, dest, original=...)`;
- `differences` (names what a rebuild does not keep).

It rests on four additions to `rda.py`:

- `CRdaWriter.add_empty(flags)`: a block that lists no file. The games' tools leave some at the end of an archive
  (`eng2.rda` ends in five; 2070's `patch1` to `patch8` hold 18 to 53 each, 44 in `patch3`), and the archive's bytes
  include them.
- `CRdaWriter(allow_empty=True)`: members of size 0 are written (the shipped archives hold a few, with offset 0 and no
  stored bytes); without it they stay refused.
- `rda.SStored(archive, entry)`: a member taken as it lies in another archive. The games' tool does not compress like
  zlib (no level 0 to 9 reproduced a member), so a rebuilt archive keeps its stored bytes only this way. A `Stored`
  member shares a copy only as it did in its archive.
- `CRdaWriter(reuse=...)`: a directory, or a resident block's blob, that comes out as one the original stored is written
  as the original stored it (keyed by the BLAKE2 digest of the decoded bytes and the flags). Otherwise it is compressed
  at level 1. `CRdaArchive.block_members`, `directory_of`, `blob_of` and `stored_bytes` give the writer's inputs.

With these, every 2070 archive tried was written again to **the same bytes** from its members alone (read only). The
archives are `Data0` to `Data5`, `addon0`, `addon1`, `eng2`, `eng7`, `exedata` and `patch1` to `patch9`. Two of 1404's
and two of 2205's were written again the same way. `tests/formats/test_rdalayout.py` has the same on synthetic archives, and
`tests/suite/test_archivemod.py` one shipped archive behind `ANNO2070_DIR`.

Note: this section was written on 2026-11-19.

## The command line

`anno-toolbox rda list|info|extract|pack` (`commands.md`, `rda`) wraps the reader and the writers.

- `list` and `info` detect the version.
- `extract` refuses a member whose path leaves the target folder.
- `pack` takes `--version 1.1|2.0|2.2` and `--flags` (a mix of `stored`, `compressed`, `encrypted`, `resident`).

The tests are in `tests/formats/test_rda_cli.py`.
