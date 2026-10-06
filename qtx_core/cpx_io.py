from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
import math
import uuid
import base64
import zlib
import hashlib
import copy
import xml.etree.ElementTree as ET

from .models import Sample
from .colorimetry import reflectance_to_xyz_lab, normalize_illuminant, xyz_to_lab


@dataclass(frozen=True)
class CpxProject:
    palette_name: str
    created: str
    columns: int
    rows: int
    tile_width: int
    tile_height: int
    gap_x: int
    gap_y: int
    illuminant: str
    observer: int
    slots: tuple[Sample | None, ...]
    source_snapshot_b64: str = ''
    source_sha256: str = ''

    @property
    def samples(self) -> tuple[Sample, ...]:
        return tuple(s for s in self.slots if s is not None)


def _float(text: str | None, default: float = 0.0) -> float:
    try:
        return float((text or '').strip())
    except Exception:
        return float(default)


def _int_attr(elem: ET.Element | None, key: str, default: int) -> int:
    if elem is None:
        return default
    try:
        return int(float(elem.attrib.get(key, default)))
    except Exception:
        return default


def _observer_from_text(text: str | None) -> int:
    t=(text or '').strip().lower()
    return 2 if '2' in t else 10


def _sample_from_element(elem: ET.Element, source_file: str, index: int, illuminant: str, observer: int) -> Sample | None:
    name=(elem.findtext('Name') or '').strip()
    if not name:
        return None
    attrs={}
    attrs_node=elem.find('Attributes')
    if attrs_node is not None:
        for a in attrs_node.findall('Attribute'):
            key=(a.attrib.get('id') or '').strip()
            if key:
                attrs[f'ATTR_{key}']=(a.text or '').strip()

    spectrum=elem.find('Spectrum')
    wavelengths: list[int]=[]; reflectance: list[float]=[]
    raw=dict(attrs)
    if spectrum is not None:
        raw['CPX_BANDPASS']=spectrum.attrib.get('Bandpass','')
        for sp in spectrum.findall('Sp'):
            try:
                wavelength = int(float(sp.attrib['Wv']))
                value = float((sp.text or '').strip())
                if not math.isfinite(value):
                    raise ValueError('non-finite reflectance')
            except (ValueError, KeyError, OverflowError) as exc:
                raise ValueError(f'CPX 样本 {name} 的光谱点无效') from exc
            wavelengths.append(wavelength)
            reflectance.append(value)
        details=spectrum.find('Details')
        if details is not None:
            for d in list(details):
                raw[f'CPX_{d.tag.upper()}']=(d.text or '').strip()

    xyz_node=elem.find('XYZ')
    lab_node=elem.find('Lab')
    xyz=(
        _float(xyz_node.findtext('X') if xyz_node is not None else None, -1.0),
        _float(xyz_node.findtext('Y') if xyz_node is not None else None, -1.0),
        _float(xyz_node.findtext('Z') if xyz_node is not None else None, -1.0),
    )
    lab=(
        _float(lab_node.findtext('L') if lab_node is not None else None, 0.0),
        _float(lab_node.findtext('a') if lab_node is not None else None, 0.0),
        _float(lab_node.findtext('b') if lab_node is not None else None, 0.0),
    )
    # Sample's cached coordinates have a fixed D65/10° contract; CPX caches
    # belong to its ViewingConditions and must not be relabelled as D65/10°.
    default_condition = normalize_illuminant(illuminant) == 'D65' and observer == 10
    valid_xyz = all(math.isfinite(v) and v >= 0 for v in xyz)
    valid_lab = lab_node is not None and all(
        lab_node.findtext(k) is not None for k in ('L', 'a', 'b')
    ) and all(math.isfinite(v) for v in lab)
    if not default_condition or not valid_xyz:
        if len(reflectance) < 2:
            raise ValueError(f'CPX 样本 {name} 缺少光谱，无法建立 D65/10° 数据')
        xyz, lab = reflectance_to_xyz_lab(reflectance, 'D65', wavelengths, 10)
    elif not valid_lab:
        lab = xyz_to_lab(xyz, 'D65', 10)

    raw['CPX_MODIFIED']=(elem.findtext('Modified') or '').strip()
    raw['CPX_SLOT_INDEX']=str(index)
    raw['CPX_VIEWING_ILLUMINANT']=illuminant
    raw['CPX_VIEWING_OBSERVER']=str(observer)
    sid=str(uuid.uuid5(uuid.NAMESPACE_URL, f'{Path(source_file).resolve()}|cpx|{index}|{name}'))
    return Sample(
        sample_id=sid,
        display_name=name,
        kind='STD',
        xyz_d65_10=tuple(float(x) for x in xyz),
        lab_d65_10=tuple(float(x) for x in lab),
        reflectance=tuple(reflectance),
        wavelengths=tuple(wavelengths) if wavelengths else tuple(range(360,701,10)),
        source_file=str(Path(source_file).resolve()),
        viewing=f'{illuminant} / {observer}° · CPX',
        raw=raw,
    )


def parse_cpx_file(path: str | Path) -> CpxProject:
    source=Path(path)
    raw_bytes=source.read_bytes()
    root=ET.fromstring(raw_bytes)
    if root.tag != 'SampleList':
        raise ValueError('不是受支持的 ChromaShare CPX（根节点应为 SampleList）')
    layout=root.find('Layout')
    tile_count=layout.find('TileCount') if layout is not None else None
    tile_size=layout.find('TileSize') if layout is not None else None
    tile_gap=layout.find('TileGap') if layout is not None else None
    columns=max(1,_int_attr(tile_count,'x',10)); rows=max(1,_int_attr(tile_count,'y',1))
    vc=root.find('ViewingConditions')
    illuminant=(vc.findtext('Illuminant') if vc is not None else 'D65') or 'D65'
    observer=_observer_from_text(vc.findtext('Observer') if vc is not None else 'd10')
    slot_elems=list(root.findall('./Samples/Sample'))
    expected=columns*rows
    # ChromaShare represents blank positions as empty <Sample> entries. Preserve the
    # entire slot array so import -> edit -> export round-trips the original layout.
    slots=[]
    for i,elem in enumerate(slot_elems):
        slots.append(_sample_from_element(elem,str(source),i,illuminant,observer))
    if len(slots) < expected:
        slots.extend([None]*(expected-len(slots)))
    elif len(slots) > expected:
        rows=math.ceil(len(slots)/columns)
        slots.extend([None]*(columns*rows-len(slots)))
    return CpxProject(
        palette_name=(root.findtext('PaletteName') or source.stem).strip() or source.stem,
        created=(root.findtext('Created') or '').strip(),
        columns=columns, rows=rows,
        tile_width=_int_attr(tile_size,'x',84), tile_height=_int_attr(tile_size,'y',34),
        gap_x=_int_attr(tile_gap,'x',20), gap_y=_int_attr(tile_gap,'y',22),
        illuminant=illuminant.strip() or 'D65', observer=observer,
        slots=tuple(slots),
        source_snapshot_b64=base64.b64encode(zlib.compress(raw_bytes,9)).decode('ascii'),
        source_sha256=hashlib.sha256(raw_bytes).hexdigest(),
    )


def _rgb_from_lab(lab: tuple[float,float,float]) -> tuple[int,int,int]:
    from .preview import lab_to_srgb_preview
    return lab_to_srgb_preview(lab, strategy='clip').rgb8


def _text(parent: ET.Element, tag: str, value) -> ET.Element:
    e=ET.SubElement(parent,tag); e.text=str(value); return e


def _blank_sample(parent: ET.Element, modified: str) -> None:
    s=ET.SubElement(parent,'Sample'); _text(s,'Name',''); _text(s,'Modified',modified)
    xyz=ET.SubElement(s,'XYZ'); [_text(xyz,t,'-1.00000') for t in ('X','Y','Z')]
    lab=ET.SubElement(s,'Lab'); [_text(lab,t,'0.00000') for t in ('L','a','b','C','h')]
    rgb=ET.SubElement(s,'RGB'); [_text(rgb,t,'0') for t in ('R','G','B')]


def _qtx_measurement_details(sample: Sample) -> dict[str,str]:
    raw=dict(sample.raw or {}); prefix='STD' if sample.kind=='STD' else 'BAT'
    params={}
    for part in str(raw.get(f'{prefix}_MEASDLL_PARAMS','')).split(','):
        if ':' in part:
            k,v=part.split(':',1); params[k.strip()]=v.strip()
    measured=raw.get('CPX_MEASURED','')
    if not measured:
        stamp=str(raw.get(f'{prefix}_DATETIME','')).strip().rstrip(',')
        if stamp:
            try:
                measured=datetime.fromtimestamp(float(stamp),timezone.utc).isoformat().replace('+00:00','Z')
            except Exception:pass
    return {
        'InstrumentModel': str(raw.get('CPX_INSTRUMENTMODEL') or params.get('Model') or raw.get('ATTR_Model') or ''),
        'InstrumentSerial': str(raw.get('CPX_INSTRUMENTSERIAL') or raw.get(f'{prefix}_INSTRUMENT_SERIAL_NO') or params.get('Serial number') or raw.get('ATTR_Serial number') or ''),
        'InstrumentSettings': str(raw.get('CPX_INSTRUMENTSETTINGS') or sample.viewing or ''),
        'Measured': str(measured or ''),
    }


def export_cpx_file(
    path: str | Path,
    palette_name: str,
    slots: Iterable[Sample | None],
    columns: int,
    rows: int | None = None,
    tile_size: tuple[int,int]=(84,34),
    tile_gap: tuple[int,int]=(20,22),
    illuminant: str='D65',
    observer: int=10,
    source_templates: dict[str,str] | None = None,
    root_template_source: str | None = None,
    preserve_source_if_unchanged: bool = False,
) -> None:
    path=Path(path); columns=max(1,int(columns or 1)); slots=list(slots)
    rows=max(1,int(rows or math.ceil(max(1,len(slots))/columns)))
    total=columns*rows
    if len(slots)<total: slots.extend([None]*(total-len(slots)))
    elif len(slots)>total:
        rows=math.ceil(len(slots)/columns); total=rows*columns; slots.extend([None]*(total-len(slots)))
    now=datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
    source_templates=dict(source_templates or {})
    root_snapshot=source_templates.get(str(root_template_source or ''))
    def decode_snapshot(blob):
        if not blob:return None
        try:return zlib.decompress(base64.b64decode(blob.encode('ascii')))
        except Exception:return None
    if preserve_source_if_unchanged and root_snapshot:
        original=decode_snapshot(root_snapshot)
        if original is not None:
            path.write_bytes(original); return
    template_roots={}
    for src,blob in source_templates.items():
        data=decode_snapshot(blob)
        if data is None:continue
        try:template_roots[str(Path(src).resolve())]=ET.fromstring(data)
        except Exception:continue
    root=template_roots.get(str(Path(root_template_source).resolve())) if root_template_source else None
    root=copy.deepcopy(root) if root is not None else ET.Element('SampleList')
    if root.tag!='SampleList':root=ET.Element('SampleList')
    pn=root.find('PaletteName')
    if pn is None:pn=ET.Element('PaletteName');root.insert(0,pn)
    pn.text=str(palette_name)
    if root.find('Created') is None:_text(root,'Created',now)
    layout=root.find('Layout')
    if layout is None:layout=ET.SubElement(root,'Layout',{'mode':'Fixed'})
    layout.attrib['mode']=layout.attrib.get('mode','Fixed') or 'Fixed'
    for tag,attrs in [('TileCount',{'x':str(columns),'y':str(rows)}),('TileSize',{'x':str(int(tile_size[0])),'y':str(int(tile_size[1]))}),('TileGap',{'x':str(int(tile_gap[0])),'y':str(int(tile_gap[1]))})]:
        node=layout.find(tag)
        if node is None:node=ET.SubElement(layout,tag)
        node.attrib.update(attrs)
    vc=root.find('ViewingConditions')
    if vc is None:vc=ET.SubElement(root,'ViewingConditions')
    il=vc.find('Illuminant')
    if il is None:il=ET.SubElement(vc,'Illuminant')
    il.text=str(illuminant)
    ob=vc.find('Observer')
    if ob is None:ob=ET.SubElement(vc,'Observer')
    ob.text=f'd{int(observer)}'
    samples_node=root.find('Samples')
    if samples_node is None:samples_node=ET.SubElement(root,'Samples')
    for child in list(samples_node):samples_node.remove(child)
    template_samples={src:list(rt.findall('./Samples/Sample')) for src,rt in template_roots.items()}
    root_src=str(Path(root_template_source).resolve()) if root_template_source else ''
    root_sample_templates=template_samples.get(root_src) or []
    for slot_index,sm in enumerate(slots):
        if sm is None:
            # Preserve the original blank CPX slot element whenever possible.
            # Some vendor CPX files attach additional metadata even to blank
            # positions; rebuilding a generic blank would silently discard it.
            if 0 <= slot_index < len(root_sample_templates):
                original_blank=root_sample_templates[slot_index]
                if not (original_blank.findtext('Name') or '').strip():
                    samples_node.append(copy.deepcopy(original_blank)); continue
            _blank_sample(samples_node,now); continue
        raw=dict(sm.raw or {})
        preserved=None
        try:
            src=str(Path(sm.source_file).resolve()); idx=int(raw.get('CPX_SLOT_INDEX','-1'))
            elems=template_samples.get(src) or []
            if 0<=idx<len(elems):preserved=copy.deepcopy(elems[idx])
        except Exception:preserved=None
        if preserved is not None and (preserved.findtext('Name') or '').strip():
            original_rt = template_roots.get(src)
            original_vc = original_rt.find('ViewingConditions') if original_rt is not None else None
            old_illuminant = (original_vc.findtext('Illuminant') if original_vc is not None else 'D65') or 'D65'
            old_observer = _observer_from_text(original_vc.findtext('Observer') if original_vc is not None else 'd10')
            if normalize_illuminant(old_illuminant) != normalize_illuminant(illuminant) or old_observer != int(observer):
                from .analysis import color_result
                result = color_result(sm, illuminant, observer)
                for tag, fields, values in [('XYZ', ('X','Y','Z'), result.xyz), ('Lab', ('L','a','b'), result.lab)]:
                    node = preserved.find(tag)
                    if node is None: node = ET.SubElement(preserved, tag)
                    for field, value in zip(fields, values):
                        child = node.find(field)
                        if child is None: child = ET.SubElement(node, field)
                        child.text = f'{value:.5f}'
                lab_node = preserved.find('Lab')
                L, a, b = result.lab
                for field, value in [('C', math.hypot(a,b)), ('h', math.degrees(math.atan2(b,a)) % 360)]:
                    child = lab_node.find(field)
                    if child is None: child = ET.SubElement(lab_node, field)
                    child.text = f'{value:.5f}'
            s=preserved; samples_node.append(s)
            attrs_node=s.find('Attributes')
            if attrs_node is not None:
                for a in attrs_node.findall('Attribute'):
                    if (a.attrib.get('id') or '').strip().casefold()=='rank':a.text=f'{slot_index % columns + 1}-{slot_index // columns + 1}'
            continue
        s=ET.SubElement(samples_node,'Sample'); _text(s,'Name',sm.display_name)
        _text(s,'Modified',raw.get('CPX_MODIFIED') or now)
        attrs=[(str(k)[5:],v) for k,v in raw.items() if str(k).startswith('ATTR_') and str(k)[5:].strip()]
        if attrs:
            # ChromaShare exports Rank as column-row (e.g. 10-7). If Rank is part
            # of the source attributes, keep it synchronised with the edited layout.
            rank_value=f'{slot_index % columns + 1}-{slot_index // columns + 1}'
            an=ET.SubElement(s,'Attributes')
            for key,val in attrs:
                if key.casefold()=='rank':val=rank_value
                a=ET.SubElement(an,'Attribute',{'id':key}); a.text=str(val)
        if sm.has_spectrum():
            spn=ET.SubElement(s,'Spectrum',{'Bandpass':raw.get('CPX_BANDPASS','Corrected') or 'Corrected'})
            for w,r in zip(sm.wavelengths,sm.reflectance):
                sp=ET.SubElement(spn,'Sp',{'Wv':str(int(w))}); sp.text=f'{float(r):.6f}'
            details=ET.SubElement(spn,'Details')
            detail_map=_qtx_measurement_details(sm)
            for tag,val in detail_map.items():
                if val not in (None,''): _text(details,tag,val)
        from .analysis import color_result
        result = color_result(sm, illuminant, observer)
        xyz=ET.SubElement(s,'XYZ')
        for tag,val in zip(('X','Y','Z'),result.xyz): _text(xyz,tag,f'{float(val):.5f}')
        L,a,b=result.lab; C=math.hypot(a,b); h=math.degrees(math.atan2(b,a))%360
        lab=ET.SubElement(s,'Lab')
        for tag,val in [('L',L),('a',a),('b',b),('C',C),('h',h)]: _text(lab,tag,f'{float(val):.5f}')
        rgb=ET.SubElement(s,'RGB')
        for tag,val in zip(('R','G','B'),_rgb_from_lab(sm.lab_d65_10)): _text(rgb,tag,str(val))
    try:
        ET.indent(root,space='  ')
    except Exception:
        pass
    tree=ET.ElementTree(root)
    tree.write(path,encoding='utf-8',xml_declaration=True)
