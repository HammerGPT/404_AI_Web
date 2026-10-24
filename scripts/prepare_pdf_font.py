"""Build a PDF font without duplicate radical-to-glyph Unicode mappings.

Chromium can choose the radical alias in ToUnicode tables when a CJK font maps
both a unified ideograph and a radical to the same glyph. Keep unified mappings
so copied Chinese text retains its original characters. Source font: Noto (OFL).
"""
import sys
from pathlib import Path
from fontTools.ttLib import TTCollection


def prepare(source, destination):
    collection=TTCollection(source)
    font=next(f for f in collection.fonts if any(n.nameID==1 and n.toUnicode()=='Noto Sans CJK SC' for n in f['name'].names))
    for table in font['cmap'].tables:
        if table.isUnicode():
            table.cmap={code:glyph for code,glyph in table.cmap.items()
                        if not (0x2E80<=code<=0x2FFF or 0xF900<=code<=0xFAFF or 0x2F800<=code<=0x2FA1F)}
    labels={1:'404 AI Report',2:'Regular',4:'404 AI Report Regular',6:'AI404Report-Regular',16:'404 AI Report',17:'Regular'}
    for name in font['name'].names:
        if name.nameID in labels:
            name.string=labels[name.nameID].encode(name.getEncoding())
    destination=Path(destination)
    destination.parent.mkdir(parents=True,exist_ok=True)
    font.save(destination)


if __name__=='__main__':
    prepare(sys.argv[1],sys.argv[2])
