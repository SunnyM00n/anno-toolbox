"""Stand-ins for the text editor: language files and their twins."""

from __future__ import annotations

from collections.abc import Callable

from annotoolbox.assetedit.gamefiles import LAYOUTS, folder_files
from annotoolbox.textedit.editfile import CEditFile
from annotoolbox.textedit.fontchain import SStyleFaces
from annotoolbox.textedit.textdoc import CTextDoc

XML_117 = (
    "<TextExport>\r\n  <Texts>\r\n"
    "    <Text>\r\n      <LineId>-10</LineId>\r\n      <Text>Save &amp; exit {0} &lt;b&gt;now&lt;/b&gt;</Text>\r\n    </Text>\r\n"
    "    <Text>\r\n      <LineId>-11</LineId>\r\n      <Text>Two\r\nlines</Text>\r\n    </Text>\r\n"
    "    <Text>\r\n      <LineId>-12</LineId>\r\n      <Text/>\r\n    </Text>\r\n"
    "    <Text>\r\n      <LineId>-10</LineId>\r\n      <Text>Save &amp; exit {0} &lt;b&gt;now&lt;/b&gt;</Text>\r\n    </Text>\r\n"
    "    <Text>\r\n      <LineId>-13</LineId>\r\n      <Text>Wood</Text>\r\n    </Text>\r\n"
    "  </Texts>\r\n</TextExport>"
)


GERMAN_117 = (
    "<TextExport>\r\n  <Texts>\r\n"
    "    <Text>\r\n      <LineId>-10</LineId>\r\n      <Text>Speichern &amp; beenden {0} &lt;b&gt;jetzt&lt;/b&gt;</Text>\r\n    </Text>\r\n"
    "    <Text>\r\n      <LineId>-11</LineId>\r\n      <Text>Two\r\nlines</Text>\r\n    </Text>\r\n"
    "    <Text>\r\n      <LineId>-12</LineId>\r\n      <Text></Text>\r\n    </Text>\r\n"
    "    <Text>\r\n      <LineId>-13</LineId>\r\n      <Text>Holz</Text>\r\n    </Text>\r\n"
    "  </Texts>\r\n</TextExport>"
)


METADATA = (
    "<TextExport><Texts><Text><LineId>-10</LineId><Name>Menu Save Button</Name><FolderId>5</FolderId></Text>"
    "</Texts></TextExport>"
)


def write_files(root, files: dict[str, bytes | str]):
    for path, data in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return folder_files(root, root / "cache")


def utf16_bytes(text: str) -> bytes:
    return b"\xff\xfe" + text.encode("utf-16-le")


# the document
def text_doc(files, layout="anno117", target="german"):
    return CTextDoc(files, LAYOUTS[layout], CEditFile(layout), "english", target)


# 117's text groups and twin lines
META_TWINS = (
    "<TextExport><Texts>"
    "<Text><LineId>-20</LineId><Name>Quest Hello</Name><FolderId>-777</FolderId></Text>"
    "<Text><LineId>-21</LineId><Name>Quest Hello</Name><FolderId>-777</FolderId><MasterId>-20</MasterId><VariationId>-900</VariationId></Text>"
    "<Text><LineId>-22</LineId><Name>Quest Hello</Name><FolderId>-777</FolderId><MasterId>-20</MasterId><VariationId>-901</VariationId></Text>"
    "<Text><LineId>-23</LineId><Name>Other</Name><FolderId>-778</FolderId></Text>"
    "</Texts></TextExport>"
)


def text_export(*items: tuple[str, str]) -> str:
    body = "".join(f"<Text><LineId>{k}</LineId><Text>{v}</Text></Text>" for k, v in items)
    return f"<TextExport><Texts>{body}</Texts></TextExport>"


# 117 and 2070
AUDIO = (
    "<Group><Assets><Asset><Template>Audio</Template><Values><Standard><GUID>1</GUID><Name>Line_A</Name></Standard>"
    "<Audio><VoiceMood>Neutral</VoiceMood><CharacterId>-555</CharacterId></Audio>"
    "<WwiseStandard><WwiseID>77</WwiseID><OasisScriptId>-20</OasisScriptId></WwiseStandard></Values></Asset>"
    "<Asset><Template>Audio</Template><Values><Standard><GUID>2</GUID><Name>Line_B</Name></Standard>"
    "<Audio><CharacterId>-999</CharacterId></Audio>"
    "<WwiseStandard><WwiseID>78</WwiseID><OasisScriptId>-23</OasisScriptId></WwiseStandard></Values></Asset></Assets></Group>"
)


PORTRAIT = (
    "<Assets><Asset><Template>Portrait</Template><Values><Portrait><PortraitName>-1</PortraitName>"
    "<OasisCharacterId>-555</OasisCharacterId></Portrait><Standard><GUID>9</GUID>"
    "<Name>Advisor Character (Ben Baalion) Portrait</Name></Standard></Values></Asset>"
    "<Asset><Template>Portrait</Template><Values><Portrait><OasisCharacterId>-900</OasisCharacterId></Portrait>"
    "<Standard><GUID>10</GUID><Name>Campaign Char01 (Marcus Africanus)</Name></Standard></Values></Asset></Assets>"
)


class FakeFontChain:
    """``CFontChain`` for 117: the styles of a language, each with the files it tries (``styles_of`` makes them)."""

    available = True

    def __init__(self, styles_of: Callable[[str], list[SStyleFaces]]) -> None:
        self.m_styles_of = styles_of

    def styles(self, language: str) -> list[SStyleFaces]:
        return self.m_styles_of(language)
