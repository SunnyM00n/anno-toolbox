"""The layout versions of the History Editions' Phoenix files (Anno 1404 and Anno 1701 as re-released), and of Anno
1800's earliest editor files (``EARLY_1800``).

Their files (``data/ui/studio/gamedata/``) store nearly every type in an older layout than 117's or 1800's schema
describes. ``LAYOUTS`` gives each such (record hash, layout GUID) its stored fields in file order, ``SAME`` the
versions whose fields are the current schema's, ``PARENTS`` the records whose parent was another one then, ``TYPES``
the two types neither schema has; ``build`` turns them into schema field records. Every entry was proven by decoding
and re-encoding all 732 files byte for byte (docs/formats/phoenix-scene-format.md, "The History Editions' files").
"""

from __future__ import annotations

import re
import zlib

# A field is a name or a field hash of the record in the 117 or 1800 schema (``record/name``: of that record), or
# (name, type code[, element type]) for a field neither has; a lower-case hyphenated name is ours, not the game's.
_HEX = re.compile(r"[0-9a-f]{8}")

# fmt: off
NAMES = {  # field hashes the schemas leave unnamed, named by the games' editor documents (the CRC32 matches)
    "c3fa0951": "NoAutoGameObjectCreationCustomPropertyIds", "6ab6e607": "UniqueDataProviderContainers",
    "ca35751c": "UniquePropertyContainers", "46a17a33": "ColorPaletteEntryUniqueId",
    "b72f6622": "IsPreciseLineSpacingEnabled", "fe536d30": "IsPreciseLetterSpacingEnabled",
    "18c37931": "UseGlyphIndexEnabled", "1c2c57ba": "PhoenixInternalId", "ee3f38b4": "IsPositionSet",
}
_GRAFFITI_STYLE = ("LocalizedFont", "Size", "BaseColor", "Bold", "Italic", "Thickness", "Gradient", "InnerSize",
                   "InnerGradient", "InnerOffset", "InnerColor", "OuterSize", "OuterGradient", "OuterOffset",
                   "OuterColor")
_GRAFFITI_FONT = ("FontRasterizedSize", "Padding", "DFEnabled", "Spread", "MultFactor", "MetricSystem", "6d51158d",
                  "b72f6622", "fe536d30", "18c37931")
_ENUM = "0xb00"

TYPES = {  # type hash -> (name, parent, layout GUID, fields): the text style and font assets of these games
    "cd6d9d63": ("TextStyleAsset", "c36e7558", "8eb9999a-bb2e-698d-b9f7-afe8116e5dc1",
                 tuple(f"afad631b/{n}" for n in _GRAFFITI_STYLE)),
    "0c3c261e": ("FontAsset", "c36e7558", "e02e6037-91b3-6e1e-9019-51c8b7e4dbc1",
                 ("d90a3eef/FontId", *(f"f1f64b8d/{n}" for n in _GRAFFITI_FONT))),
}

LAYOUTS: dict[tuple[str, str], tuple] = {
    ("0c454dec", "9f985b52-7e48-9a2d-a33f-4d5c81841095"): (  # WidgetInstance: anchor constraints, no name string
        "Position", "PivotPoint", "RotationAngle", "Scale", ("HorizontalAnchorConstraint", _ENUM),
        ("HorizontalAnchorMetrics", _ENUM), ("VerticalAnchorConstraint", _ENUM), ("VerticalAnchorMetrics", _ENUM),
        ("HorizontalResizeMetrics", _ENUM), ("VerticalResizeMetrics", _ENUM), ("KeepAspectRatio", "0x100"),
        "ColorTint", "AlphaTint", "BlendingMode", "Visible", "Masked", "MaskMode", "IsHidden", "PropertyValues",
        "GameObjectTemplate", "EnumEntry", "Effects", "Components", "NameCrc", "ZIndex", "IsStatic"),
    ("0f561333", "a9994114-44c2-fedd-ffec-e020b06a6ab7"): (  # ContainerWidgetInstance
        "Template", ("CurrentGameObject", "0x4300", "8f8600a5"), "CurrentEnumValue", "c3fa0951",
        ("DynamicProperties", "0x1f01")),
    ("c19f99dd", "9e996ead-e6c4-0f4d-923d-eb17752d41c9"): (  # ImageWidgetInstance
        "Image", "DynamicImageId", "TextureAddressingMode", "UVOffset", "UVScale", "UVRotation", "DynamicImageSizing",
        "FillMode"),
    ("41a404e5", "705f5a04-c688-cad0-1ea3-04bacfef4089"): (  # LabelWidgetInstance
        "Text", "Html", "LocalizationId", "PostProcessText", "AllowResize", "TextStyle", "AlignmentX", "FlipWhenRTL",
        "AlignmentY", "LineSpacing", "LetterSpacing", "RelativeOffsetY", "AbsoluteOffsetY", "TextPlane",
        "HorizontalOverflow", "VerticalOverflow"),
    ("8eeb57e6", "6bb57e2c-0d47-068d-3df1-138517046606"): (  # VideoWidgetInstance: 1800's, no IsView
        "Video", "DynamicVideoId", "AutoStart", "Repeat", "Volume", "Mute", "e9fe6e00", "DynamicVideoSizing",
        "FillMode"),
    ("da06bd6e", "9fb62ff2-d074-846e-66b4-73903920785a"): (  # ContainerWidgetTemplate: no GroupTemplate record
        "CustomProperties", "2f208351/WidgetInstances", "DataBinderInstances", "AnimationInstances", "WidgetType",
        "DataSourceObjectTemplate", "Components", "6ab6e607", "ca35751c", "ReferenceSize", "ForceReferenceSize",
        "BackgroundColor", "Enum"),
    ("1795cb2b", "8c6d9295-1ba0-2c9e-9fbe-6561b7b73888"): ("ColorPalette",),  # SceneTemplate
    ("17afa8e7", "b13e36cb-ddf7-91a8-f5af-da2d7ace64ec"): (  # WidgetTypeTemplate: 1800's
        "IsAbstract", "ParentWidgetTypeCategory", "ParentWidgetType", "CustomProperties"),
    ("84f12f7b", "9b22046e-d7f3-c8dc-ea46-b7d284aad24e"): ("068cd567/CustomProperties",),  # ComponentTemplate
    ("c0f0995c", "be927a99-ea38-4e80-3930-d4790a503cf3"): (),  # EffectTemplate, under GameObjectTemplate
    ("8f8600a5", "79de75f7-e9b4-789d-23e6-1680f7ea3c7a"): ("068cd567/CustomProperties",),  # GameObjectTemplate
    ("1294a820", "e23bb15e-a8bb-7ac2-fd39-fe2f054425a4"): (  # CustomPropertyInstance
        "UniqueId", ("AtomPropertyId", "0x800"), "PropertyType", "DefaultValue", "ObjectTemplate", "EnumAsset",
        "PropertyAccessibility", "PropertyNameCrc", "c7501213"),
    ("51a18126", "f9167ace-70fd-1df2-a853-390b55116cfd"): ("UniqueId", "ComponentTemplate"),  # ComponentInstance
    ("15a03701", "9f6e01ca-3d6f-48af-64eb-29cd34f793da"): ("UniqueId", "EffectTemplate"),  # EffectInstance
    ("b03e911f", "820d8720-9a5c-5c84-7af1-c4da2f7ba741"): ("FrameRate", "Duration", "Tracks", "NameCrc"),
    ("edcf0433", "ca6c5af3-7fe3-5e43-5fe3-aec198018924"): (  # DataBinderInstance: 1800's
        "ConverterInstance", "SourcePropertyIndices", "DestinationPropertyIndices", "GameObjectTemplate", "EnumEntry",
        "IsStatic"),
    ("1bb72d66", "bdede506-6fa5-d92d-faf0-6d9db597f985"): ("PropertyPath", "DataProviderIndex"),  # PropertyContainer
    ("0019dd69", "33e4946a-ed80-0bc7-8b20-26d9f353862a"): (  # ImageAtlasAsset
        "ImageId", ("ImageDescriptions", "0x4201", "f3d253c2")),
    ("6dcd0e65", "f5adb901-b549-7c11-ab63-5bf896dd4c00"): (  # LocalizedFontAsset
        "DefaultFont", "FontDescriptor", "ScalingFactor", "OnlyUseOfflineCharacters", "LanguagesExceptions"),
    ("4674a895", "2baf5f6d-cd9c-dfab-89f0-60601a8ca32c"): (  # LanguageFontException
        "Language", "FontException", "FontDescriptor", "ScalingFactor", "OnlyUseOfflineCharacters"),
    ("437ab854", "10ca3368-4b7d-2475-8464-231faf2e9801"): ("Color", "46a17a33"),  # ColorPaletteEntry, no name
    ("391b8083", "b8a3a30c-f69e-ebba-ac5b-16af8d39a3d5"): (  # ColorTheme: an id before its values
        "ThemeName", ("theme-id", "0x800"), "ColorThemeValues"),
}

# Anno 1800's earliest editor files (406 of its 6,870 did not decode): layout versions found by trying the field lists
# near the known ones on the files that use them, each kept only where files re-encode byte for byte with it; where two
# fields of one type could change places the order of 117's, 1800's or the History Editions' list is taken
# (docs/formats/phoenix-scene-format.md, "The History Editions' files", 1800's earliest files)
EARLY_1800: dict[tuple[str, str], tuple] = {
    ("da06bd6e", "b3961ba1-f8bc-1936-151f-d421b0add844"): (  # ContainerWidgetTemplate: 149 files
        "CustomProperties", "2f208351/WidgetInstances", "DataBinderInstances", "AnimationInstances", "WidgetType",
        "DataSourceObjectTemplate", "Components", "6ab6e607", "ca35751c", "ReferenceSize", "ForceReferenceSize",
        "BackgroundColor", "Enum", "c0f5e482"),
    ("1294a820", "97539330-c052-d578-42c4-a09712baa684"): (  # CustomPropertyInstance: 100 files
        ("AtomPropertyId", "0x800"), "UniqueId", "PropertyType", "DefaultValue", "ObjectTemplate", "EnumAsset",
        "PropertyAccessibility", "PropertyNameCrc", "IsArrayOfAnyGameObject"),
    ("edcf0433", "cc837766-edb0-1634-a17e-8eb6f241f7af"): (  # DataBinderInstance: 68 files
        "ConverterInstance", "SourcePropertyIndices", "DestinationPropertyIndices", "EnumEntry", "IsStatic",
        "IsDynamic", "ProfileID"),
    ("41a404e5", "bbca9324-ce12-2f18-7aa1-629244d0ba68"): (  # LabelWidgetInstance: 33 files (FlipWhenRTL a byte: 2 seen)
        "Text", "DisplayedText", "Html", "LocalizationId", "PostProcessText", "LoadableAssetGroup", "TextStyle",
        ("FlipWhenRTL", "0x400"), "AlignmentY", "LineSpacing", "Leading", "LetterSpacing", "RelativeOffsetY", "AbsoluteOffsetY",
        "TextPlane", "HorizontalOverflow", "VerticalOverflow", "MaxCharCountPerLine", "MaxLineCount",
        "DisplayedOffset", "DisplayedSize", "ActiveAreaType"),
    ("41a404e5", "bfdeb53c-07c7-d504-0021-7eccb2657bd0"): (  # LabelWidgetInstance: 28 files (a set reference read it)
        "Text", "Html", "LocalizationId", "PostProcessText", "LoadableAssetGroup", "TextStyle", "AlignmentX", "FlipWhenRTL",
        "AlignmentY", "LineSpacing", "LetterSpacing", "RelativeOffsetY", "AbsoluteOffsetY", "TextPlane",
        "HorizontalOverflow", "VerticalOverflow"),
    ("41a404e5", "1f2a9b77-be0e-a674-627e-89ddef273f4c"): (  # LabelWidgetInstance: 23 files
        "Text", "DisplayedText", "Html", "LocalizationId", "PostProcessText", "LoadableAssetGroup", "TextStyle",
        "AlignmentX", "FlipWhenRTL", "LineSpacing", "Leading", "LetterSpacing", "RelativeOffsetY", "AbsoluteOffsetY",
        "TextPlane", "HorizontalOverflow", "VerticalOverflow", "MaxCharCountPerLine", "MaxLineCount",
        "DisplayedOffset", "DisplayedSize", "ActiveAreaType"),
    ("da06bd6e", "5fb03fc0-280b-bbcf-0b45-90fdb0d7d5d4"): (  # ContainerWidgetTemplate: 12 files
        "CustomProperties", "2f208351/WidgetInstances", "DataBinderInstances", "AnimationInstances", "WidgetType",
        "DataSourceObjectTemplate", "Components", "6ab6e607", "ca35751c", "ReferenceSize", "ForceReferenceSize",
        "BackgroundColor", "Enum"),
    ("da06bd6e", "6c4f1d33-5b40-0c87-2032-73725067633f"): (  # ContainerWidgetTemplate: 7 files (the order the bytes allow: an older Enum before WidgetType)
        "CustomProperties", "DataBinderInstances", "AnimationInstances", "Enum", "WidgetType",
        "DataSourceObjectTemplate", "Components", "6ab6e607", "ca35751c", "ReferenceSize", "ForceReferenceSize",
        "BackgroundColor", "c0f5e482"),
    # found from the bytes (2026-11-14): each present value read by its type code at its place in every file that
    # names the version, the field taken from the record's known lists where one fits, in their order
    ("1294a820", "bac7f775-aeed-31e6-7453-1cf651afb37f"): (  # CustomPropertyInstance: named by 100 files
        "UniqueId", ("AtomPropertyId", "0x800"), "PropertyType", "DefaultValue", "ObjectTemplate", "EnumAsset",
        "PropertyAccessibility", "PropertyNameCrc"),
    ("0c454dec", "6ae62230-5bd0-90fc-a0e9-b41e39270610"): (  # WidgetInstance: named by 75 files
        "Position", "PivotPoint", "RotationAngle", "Scale", ("HorizontalAnchorConstraint", "0xb00"),
        ("HorizontalAnchorMetrics", "0xb00"), ("VerticalAnchorConstraint", "0xb00"), ("VerticalAnchorMetrics", "0xb00"),
        ("HorizontalResizeMetrics", "0xb00"), ("VerticalResizeMetrics", "0xb00"), ("KeepAspectRatio", "0x100"),
        "ColorTint", "AlphaTint", "BlendingMode", "Visible", "Masked", "MaskMode", "IsHidden", "PropertyValues",
        "GameObjectTemplate", "EnumEntry", "Effects", "Components", "NameCrc", "ZIndex", "IsStatic"),
    ("c19f99dd", "c8baa739-4f2b-07a8-31f0-655ce1cfc033"): (  # ImageWidgetInstance: named by 69 files
        "Image", "DynamicImageId", "TextureFiltering", "TextureAddressingMode", "UVOffset", "UVScale", "UVRotation"),
    ("b0dd1389", "40d10bcc-cfbd-ded0-452e-280a1c317e72"): (  # DataProviderContainer: named by 107 files
        "DataProviderPath",),
    ("1294a820", "2cd46a09-a6a6-da8a-757c-0bd1c746ff70"): (  # CustomPropertyInstance: named by 8 files
        "UniqueId", "PropertyType", "DefaultValue", "ObjectTemplate", "EnumAsset", "PropertyAccessibility",
        "PropertyNameCrc", "c7501213", "53ea90f4"),
    ("0c454dec", "dd97177d-13ac-56df-5f6b-db0e72f1a002"): (  # WidgetInstance: named by 8 files
        "Position", "PivotPoint", "RotationAngle", "Scale", ("HorizontalAnchorConstraint", "0xb00"),
        ("HorizontalAnchorMetrics", "0xb00"), ("VerticalAnchorConstraint", "0xb00"), ("VerticalAnchorMetrics", "0xb00"),
        ("HorizontalResizeMetrics", "0xb00"), ("VerticalResizeMetrics", "0xb00"), ("KeepAspectRatio", "0x100"),
        "ColorTint", "AlphaTint", "BlendingMode", "Visible", "Masked", "MaskMode", "IsHidden", "PropertyValues",
        "GameObjectTemplate", "EnumEntry", "Effects", "Components", "NameCrc", "c7501213", "ZIndex", "IsStatic"),
    ("15a03701", "bc068c4f-0766-9757-2553-60f1d4c1e62f"): (  # EffectInstance: named by 5 files
        "UniqueId", "EffectTemplate", ("effect-objects", "0x4201")),
    ("c19f99dd", "70ca8370-d969-07b7-40e1-e6e792112185"): (  # ImageWidgetInstance: named by 7 files
        "Image", "DynamicImageId", "TextureFiltering", "TextureAddressingMode", "UVOffset", "UVScale", "UVRotation",
        "UVApplyRotationAfterScale", "DynamicImageSizing", "FillMode"),
    ("41a404e5", "1f12cab7-7a8c-9a94-5522-52cf473659c0"): (  # LabelWidgetInstance: named by 6 files
        "Text", "DisplayedText", "Html", "LocalizationId", "PostProcessText", "LoadableAssetGroup", "TextStyle",
        ("FlipWhenRTL", "0x400"), "LineSpacing", "LetterSpacing", "RelativeOffsetY", "AbsoluteOffsetY", "TextPlane",
        "HorizontalOverflow", "VerticalOverflow", "VerticalSizing", "MaxCharCountPerLine", "MaxLineCount",
        "DisplayedOffset", "DisplayedSize", "BaselineY", "ActiveAreaType"),
    ("0f561333", "b052db6a-b2ab-e5a3-9d08-6171855363c9"): (  # ContainerWidgetInstance: named by 8 files
        "Template", "CurrentEnumValue", "c3fa0951"),
    ("edcf0433", "e5c6078f-9361-e618-5489-161a6fb24eeb"): (  # DataBinderInstance: named by 7 files
        "ConverterInstance", "SourcePropertyIndices", "DestinationPropertyIndices", "GameObjectTemplate", "EnumEntry",
        "IsStatic"),
    ("0c454dec", "7d9990ae-471c-838c-219c-fea8000a5fbf"): (  # WidgetInstance: named by 2 files
        "Position", "PivotPoint", "RotationAngle", "Scale", ("HorizontalAnchorConstraint", "0xb00"),
        ("HorizontalAnchorMetrics", "0xb00"), ("VerticalAnchorConstraint", "0xb00"), ("VerticalAnchorMetrics", "0xb00"),
        ("HorizontalResizeMetrics", "0xb00"), ("VerticalResizeMetrics", "0xb00"), ("KeepAspectRatio", "0x100"),
        "ColorTint", "AlphaTint", "BlendingMode", "Visible", "Masked", "MaskMode", "IsHidden", "PropertyValues",
        "GameObjectTemplate", "EnumEntry", "Effects", "Components", "NameCrc", "c7501213", "ZIndex", "IsStatic"),
    ("41a404e5", "26f6ca55-85d0-1cbc-307c-380b90976248"): (  # LabelWidgetInstance: named by 2 files
        "Text", "DisplayedText", "Html", "LocalizationId", "PostProcessText", "LoadableAssetGroup", "TextStyle",
        "FlipWhenRTL", "LineSpacing", "LetterSpacing", "RelativeOffsetY", "AbsoluteOffsetY", "TextPlane",
        "HorizontalOverflow", "VerticalOverflow", "VerticalSizing", "MaxCharCountPerLine", "MaxLineCount",
        "DisplayedOffset", "DisplayedSize", "BaselineY", "ActiveAreaType"),
    ("1bb72d66", "efda3700-08a0-8af9-088e-9f1fe25deaa6"): (  # PropertyContainer: named by 7 files
        "PropertyPath", "DataProviderIndex"),
    ("0f561333", "7192b9fa-d848-ee9e-56f6-197e027481f4"): (  # ContainerWidgetInstance: named by 27 files
        "Template", ("CurrentGameObject", "0x4300", "8f8600a5"), "CurrentEnumValue", ("bool-after-enum-value", "0x100"),
        "c3fa0951", ("DynamicProperties", "0x1f01")),
    ("a5eee11d", "be72077e-1951-8044-6d34-773c5dddfba6"): (  # TrackInstance: named by 6 files
        "AnimatedPropertyIndex",),
    ("b03e911f", "ea58b44a-6b04-9f1b-f97e-2bd5fc3dd66b"): (  # AnimationInstance: named by 5 files
        "FrameRate", "Duration", "Tracks", "NameCrc", "c7501213"),
    # the two oldest templates and the versions only their widgets hold (2026-11-14, later): each searched on the
    # objects that use it, then proven by the whole files re-encoding
    ("0c454dec", "41121748-ca3a-e905-2d1c-1f2bd1981ce2"): (  # WidgetInstance: named by 18 files
        "Position", "PivotPoint", "RotationAngle", "Scale", ("HorizontalAnchorConstraint", "0xb00"),
        ("HorizontalAnchorMetrics", "0xb00"), ("VerticalAnchorConstraint", "0xb00"), ("VerticalAnchorMetrics", "0xb00"),
        ("HorizontalResizeMetrics", "0xb00"), ("VerticalResizeMetrics", "0xb00"), ("KeepAspectRatio", "0x100"),
        "ColorTint", "AlphaTint", "BlendingMode", "Visible", "Masked", "MaskMode", "IsHidden", "PropertyValues",
        "GameObjectTemplate", "EnumEntry", "Effects", "Components", "NameCrc", "ZIndex", "IsStatic"),
    ("0c454dec", "0eea741c-7118-8468-a783-1c89af0a8c45"): (  # WidgetInstance: named by 8 files, no enum, not static
        "Position", "PivotPoint", "RotationAngle", "Scale", ("HorizontalAnchorConstraint", "0xb00"),
        ("HorizontalAnchorMetrics", "0xb00"), ("VerticalAnchorConstraint", "0xb00"), ("VerticalAnchorMetrics", "0xb00"),
        ("HorizontalResizeMetrics", "0xb00"), ("VerticalResizeMetrics", "0xb00"), ("KeepAspectRatio", "0x100"),
        "ColorTint", "AlphaTint", "BlendingMode", "Visible", "Masked", "MaskMode", "IsHidden", "PropertyValues",
        "GameObjectTemplate", "Effects", "Components", "NameCrc", "ZIndex"),
    ("0f561333", "a71b2f9b-1479-4bbd-0ceb-60a38ec17b96"): (  # ContainerWidgetInstance: named by 10 files
        "Template", "CurrentEnumValue", "c3fa0951", ("game-object-types", "0x801"), ("DynamicProperties", "0x1f01")),
    ("0f561333", "03ddc48a-930e-b7e6-6093-1134ace79326"): (  # ContainerWidgetInstance: named by 7 files
        "Template", "c3fa0951", ("game-object-types", "0x801"), ("DynamicProperties", "0x1f01")),
    ("edcf0433", "5d538e84-f094-df96-1e17-3b48361f424e"): (  # DataBinderInstance: named by 18 files
        "ConverterInstance", "SourcePropertyIndices", "DestinationPropertyIndices", "GameObjectTemplate", "EnumEntry",
        "IsStatic"),
    ("edcf0433", "d4459e0e-2659-b1a7-4cce-68677121172e"): (  # DataBinderInstance: named by 8 files
        "ConverterInstance", "SourcePropertyIndices", "DestinationPropertyIndices", "GameObjectTemplate"),
    ("51a18126", "cfe24fa7-e1e9-a8bd-b1c8-eb121f488129"): ("UniqueId", "ComponentTemplate"),  # ComponentInstance: 6
    ("15a03701", "fc1fe73a-0ef3-64f2-c900-7a2bd339e4d0"): (  # EffectInstance: named by 6 files
        "UniqueId", "EffectTemplate", ("effect-objects", "0x4201")),
    ("da06bd6e", "a5fed3f8-b4e8-e012-893f-12a803ffbabd"): (  # ContainerWidgetTemplate: 19 files
        "CustomProperties", "2f208351/WidgetInstances", "DataBinderInstances", "AnimationInstances", "WidgetType",
        "DataSourceObjectTemplate", "Components", "6ab6e607", "ca35751c", "ReferenceSize", "ForceReferenceSize",
        "BackgroundColor", "Enum"),
    ("da06bd6e", "8d93c357-ca31-dd50-bdb3-139d44267b56"): (  # ContainerWidgetTemplate: 8 files, no enum yet
        "CustomProperties", "2f208351/WidgetInstances", "DataBinderInstances", "AnimationInstances", "WidgetType",
        "DataSourceObjectTemplate", "Components", "6ab6e607", "ca35751c", "ReferenceSize", "ForceReferenceSize",
        "BackgroundColor"),
    ("41a404e5", "f833d51a-c106-5f5c-5a00-1d2f3e21502d"): (  # LabelWidgetInstance: named by 1 file
        "Text", "DisplayedText", "Html", "LocalizationId", "PostProcessText", "LoadableAssetGroup", "TextStyle",
        "FlipWhenRTL", "AlignmentY", "LineSpacing", "Leading", "LetterSpacing", "RelativeOffsetY", "AbsoluteOffsetY",
        "TextPlane", "HorizontalOverflow", "VerticalOverflow", "MaxCharCountPerLine", "DisplayedOffset", "DisplayedSize"),
    ("8eeb57e6", "ec142015-9f1f-cccb-45ca-6f3818055d2d"): (  # VideoWidgetInstance: 3 files, 12 embedded objects
        "Video", "DynamicVideoId", "AutoStart", "Repeat", "Volume", "Mute", "e9fe6e00",
        *((f"embedded-{i}", "0x4000") for i in range(1, 13)), "DynamicVideoSizing", "FillMode"),
    ("c0f0995c", "d73e2a72-bb65-f404-492e-b8ddc2856784"): ("IsBatchable",),  # EffectTemplate, under GameObjectTemplate
}

# A version some files store with fewer fields than others of the same version (their editor did not change it):
# (record, version, the record's own field count) -> its fields in those files
COUNTED: dict[tuple[str, str, int], tuple] = {
    ("da06bd6e", "ce132fa8-5b7f-64fc-4254-13ca8385416b", 13): (  # ContainerWidgetTemplate: 3 of its 13 files
        "CustomProperties", "DataBinderInstances", "AnimationInstances", "WidgetType", "DataSourceObjectTemplate",
        "Components", "6ab6e607", "ca35751c", "ReferenceSize", "ForceReferenceSize", "BackgroundColor", "Enum",
        "c0f5e482"),
}

SAME = (  # versions whose stored fields are the current schema's
    ("2df5f3e4", "dfe61c33-9128-dde9-3e4c-5f4e0d5fe385"),  # PropertyValue
    ("32f7d270", "348e5d49-81c5-6496-eeb4-3cc1c39eda46"),  # FloatKey
    ("33d396c1", "abb336df-3912-d412-5ee1-8d95fcaa03bf"),  # ColorType
    ("521b7719", "77f284aa-57e1-99db-3eaf-5367853cfc37"),  # UInt32Key
    ("5624481e", "f1edf73e-a7a3-ddf8-dd5c-7bf0bbed3f8a"),  # EnumBindingInfo
    ("695f2292", "a101ae2e-1b0d-a20a-e42e-399eeb908dc0"),  # ConversionValue
    ("6a3903a3", "93a3a959-fdb8-da7c-14dd-d9cbc1a648a7"),  # Vector2Key
    ("6c4a2404", "ba350fca-0f61-baec-144e-3415a1a1fab3"),  # EnumToValueConverterInstance
    ("6e5a7f0e", "5643859c-eef3-10c1-b250-43b64844d9be"),  # PropertyId
    ("7338766c", "35a89a69-2220-dee5-39cb-2792e30b201a"),  # TrackKey
    ("9195460e", "c4ebae34-c66a-d932-1a6c-5126113a10ca"),  # EnumEntry
    ("a5eee11d", "965ab656-fe67-7d03-bac9-d5cbfe7a8b54"),  # TrackInstance
    ("a79767ed", "00581db0-6cd3-36fa-8fb2-25841cc4763c"),  # Color
    ("c7613662", "91256baf-bace-7a6d-42d0-4881140f66f5"),  # NineSliceScalingParams
    ("d28564c6", "4465292c-07eb-500d-eb27-be3e3617afb3"),  # Vector3Key
    ("d5cc1167", "09e7ef7e-e2ee-2cfc-7083-a5c201a06427"),  # BooleanKey
    ("d6815b4f", "53cf9514-3945-3037-bf29-faafc434ed37"),  # VideoAsset
    ("d95eca62", "ae5ad1bb-bf6c-63d0-5c19-1d6486a67cb7"),  # ColorThemeValue
    ("df70904f", "bdb21a27-4bf5-c220-88a3-296a5937a2cb"),  # ConversionRule
    ("f3d253c2", "1fb9022e-c659-8d80-b275-0e2e6c9debb9"),  # ImageDescription
    ("fa20916b", "4b63307d-8cd4-369a-d526-194388591278"),  # EnumConverterInstance
)

TRAILING = {"DynamicProperties"}  # written after the fields the count and the presence mask cover
PARENTS = {  # (record, version) -> its parent
    ("c0f0995c", "be927a99-ea38-4e80-3930-d4790a503cf3"): "8f8600a5",
    ("c0f0995c", "d73e2a72-bb65-f404-492e-b8ddc2856784"): "8f8600a5",
}
# fmt: on


def field_hash(name: str) -> str:
    """The hash of a field name (its CRC32), or the name when it is a hash already."""
    return name if _HEX.fullmatch(name) else f"{zlib.crc32(name.encode()):08x}"


def _named(f: dict) -> dict:
    return {**f, "name": f["name"] or NAMES.get(f["hash"])}


def _field(schemas: list[dict], rec: str, item) -> dict:
    """The schema field record of one ``LAYOUTS`` item of record ``rec``."""
    if isinstance(item, tuple):
        name, code, *elem = item
        h = field_hash(name)
        out = {"name": None if h == name else name, "hash": h, "code": code, "elem": elem[0] if elem else None,
               "flags": "0x2001", "offset": 1}  # fmt: skip
        if name in TRAILING:
            out["trailing"] = True
        return out
    if "/" in item:
        rec, item = item.split("/")
    h = field_hash(item)
    for schema in schemas:
        for f in schema.get(rec, {}).get("fields", []):
            if f["hash"] == h:
                return _named(f)
    raise KeyError(f"no field {item} of {rec} in the schemas")


def build(schemas: list[dict], stored) -> tuple[dict[tuple, list[dict]], dict[str, dict]]:
    """(record hash, layout GUID) -> its stored fields, and the new types as schema records. ``schemas`` are looked
    up in order for the fields named; ``stored(field)`` says whether a schema field is stored (``phoenix.is_stored``).
    A ``COUNTED`` list is keyed (record, version, its field count)."""
    lists = {**LAYOUTS, **EARLY_1800, **COUNTED}
    layouts = {key: [_field(schemas, key[0], it) for it in items] for key, items in lists.items()}
    for rec, guid in SAME:
        layouts[(rec, guid)] = [_named(f) for f in schemas[0][rec]["fields"] if stored(f)]
    types = {}
    for h, (name, parent, guid, items) in TYPES.items():
        types[h] = {"hash": h, "name": name, "parent": parent, "guid": guid,
                    "fields": [_field(schemas, h, it) for it in items]}  # fmt: skip
    return layouts, types
