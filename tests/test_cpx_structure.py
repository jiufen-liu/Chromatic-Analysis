from pathlib import Path
from qtx_core.cpx_io import parse_cpx_file, export_cpx_file


def test_minimal_cpx_roundtrip(tmp_path: Path):
    src=tmp_path/'a.cpx'
    src.write_text('''<?xml version="1.0" encoding="utf-8"?>\n<SampleList><PaletteName>T</PaletteName><Created>x</Created><Layout mode="Fixed"><TileCount x="2" y="1"/><TileSize x="84" y="34"/><TileGap x="20" y="22"/></Layout><ViewingConditions><Illuminant>D65</Illuminant><Observer>d10</Observer></ViewingConditions><Samples><Sample><Name>A</Name><Modified>x</Modified><Attributes><Attribute id="Rank">1-1</Attribute></Attributes><Spectrum Bandpass="Corrected"><Sp Wv="360">10</Sp><Sp Wv="370">11</Sp></Spectrum><XYZ><X>10</X><Y>10</Y><Z>10</Z></XYZ><Lab><L>50</L><a>1</a><b>2</b><C>2.236</C><h>63</h></Lab><RGB><R>100</R><G>100</G><B>100</B></RGB></Sample><Sample><Name></Name><Modified>x</Modified><XYZ><X>-1</X><Y>-1</Y><Z>-1</Z></XYZ><Lab><L>0</L><a>0</a><b>0</b><C>0</C><h>0</h></Lab><RGB><R>0</R><G>0</G><B>0</B></RGB></Sample></Samples></SampleList>''',encoding='utf-8')
    p=parse_cpx_file(src)
    assert p.columns==2 and p.rows==1 and len(p.samples)==1 and p.slots[1] is None
    out=tmp_path/'b.cpx'; export_cpx_file(out,p.palette_name,p.slots,p.columns,p.rows)
    p2=parse_cpx_file(out); assert p2.columns==2 and len(p2.samples)==1 and p2.slots[1] is None
