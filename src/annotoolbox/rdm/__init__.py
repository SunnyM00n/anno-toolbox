"""The RDM toolchain for modders: ``.rdm`` to glTF 2.0 and back, a listing and a diff of every element, the check
against the games' conventions, and all of it over a folder.

``rdmgltf.export`` writes the glTF document of RDM files (``rigmath`` holds the skeleton maths), ``gltfrdm.import_gltf``
reads one back (``gltfdoc`` its shared parts, ``gltfmesh`` the meshes, ``gltfskin`` skeletons and animations);
``listing`` lists and compares files, ``conventions`` holds a file against the conventions of 117's own files,
``batch`` runs a command over files and folders, ``images`` attaches textures to an export. The command line is
``commands.py``; the rule "exactly what the source holds" and the mapping: docs/rdm-gltf.md. The Toolbox's ``rdm`` command group
registers ``commands``.
"""
