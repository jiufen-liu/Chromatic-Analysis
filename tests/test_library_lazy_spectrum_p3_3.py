import json
import sqlite3
from pathlib import Path

from qtx_app.library_store import LibraryStore


def _store(path: Path) -> LibraryStore:
    store = LibraryStore.__new__(LibraryStore)
    store.path = path
    store.set_access_scope(None)
    store.perf_log_path = path.with_suffix('.log')
    return store


def _make_db(path: Path):
    db=sqlite3.connect(path)
    db.execute("CREATE TABLE saved_qtx(path TEXT PRIMARY KEY, customer TEXT NOT NULL DEFAULT '未分类', position INTEGER NOT NULL DEFAULT 0, mirror_path TEXT NOT NULL DEFAULT '')")
    db.execute("CREATE TABLE saved_samples(sample_key TEXT PRIMARY KEY, qtx_path TEXT NOT NULL, payload TEXT NOT NULL)")
    db.execute("INSERT INTO saved_qtx VALUES('x.qtx','客户X',0,'')")
    for i, refl in enumerate(([20.0,30.0,40.0],[80.0,105.0,60.0])):
        payload={
            'sample_id':str(i+1),'display_name':f'S{i+1}','kind':'STD',
            'xyz_d65_10':[1.0,2.0,3.0],'lab_d65_10':[50+i,10+i,-5-i],
            'reflectance':refl,'wavelengths':[400,500,600],
            'source_file':'legacy.qtx','viewing':'%R MAV SCI UV Cal','raw':{'ATTR_X':'Y'},
        }
        db.execute("INSERT INTO saved_samples VALUES(?,?,?)",(f'x.qtx|{i+1}','x.qtx',json.dumps(payload)))
    db.commit();db.close()


def test_library_page_default_does_not_deserialize_payload(tmp_path):
    path=tmp_path/'p3.sqlite3';_make_db(path);store=_store(path);store._create_schema()
    store._sample_from_payload=lambda payload: (_ for _ in ()).throw(AssertionError('payload should stay cold'))
    total,samples=store.library_page('客户X',limit=48)
    assert total==2
    assert [x.display_name for x in samples]==['S1','S2']
    assert all(not x.reflectance and not x.wavelengths for x in samples)
    assert samples[0].raw['__P3_LIGHTWEIGHT__']=='1'
    assert samples[1].raw['__FLUORESCENT_INDEX']=='1'


def test_full_page_path_still_hydrates_visible_payload(tmp_path):
    path=tmp_path/'p3.sqlite3';_make_db(path);store=_store(path);store._create_schema()
    total,samples=store.library_page('客户X',limit=1,lightweight=False)
    assert total==2 and len(samples)==1
    assert samples[0].reflectance==(20.0,30.0,40.0)
    assert samples[0].raw['ATTR_X']=='Y'


def test_selected_keys_hydrate_only_requested_rows(tmp_path):
    path=tmp_path/'p3.sqlite3';_make_db(path);store=_store(path);store._create_schema()
    light=store.load_index_samples_by_keys(['x.qtx|2'])
    assert len(light)==1 and light[0].reflectance==tuple()
    full=store.load_samples_by_keys(['x.qtx|2'])
    assert len(full)==1 and max(full[0].reflectance)==105.0


def test_recovery_by_sample_id_only_hydrates_matching_rows(tmp_path):
    path=tmp_path/'p3.sqlite3';_make_db(path);store=_store(path);store._create_schema()
    rows=store.load_samples_by_sample_ids({'2'})
    assert len(rows)==1
    assert rows[0].sample_id=='2'
    assert rows[0].reflectance==(80.0,105.0,60.0)


def test_has_samples_is_payload_free(tmp_path):
    path=tmp_path/'p3.sqlite3';_make_db(path);store=_store(path);store._create_schema()
    store._sample_from_payload=lambda payload: (_ for _ in ()).throw(AssertionError('payload should stay cold'))
    assert store.has_samples() is True
