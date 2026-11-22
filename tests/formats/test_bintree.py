"""``formats.bintree``: 2070's binary tag tree reads back what it writes, and a damaged table is a ``CBinTreeError``."""

import struct

import pytest

from annotoolbox.formats import bintree
from annotoolbox.formats.isd2070 import SNode


def _tree() -> bintree.STree:
    root = SNode("Root")
    root.children.append(SNode("Name"))
    root.children[0].text = "x"
    return bintree.STree([root])


def test_a_tree_reads_back_byte_for_byte():
    data = bintree.write(_tree())
    assert bintree.write(bintree.read(data)) == data


def test_a_table_that_lists_more_names_than_it_holds_is_a_bintree_error():
    data = bintree.write(_tree())
    (map_at,) = struct.unpack_from("<I", data, len(data) - 4)
    broken = bytearray(data)
    struct.pack_into("<I", broken, map_at + 4, 500)  # the name count
    with pytest.raises(bintree.CBinTreeError):
        bintree.read(bytes(broken))
