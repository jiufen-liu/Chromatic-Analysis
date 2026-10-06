import json
import sqlite3
from pathlib import Path

from qtx_app.library_store import LibraryStore


def _legacy_db(path: Path):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE saved_qtx(path TEXT PRIMARY KEY, customer TEXT NOT NULL DEFAULT '未分类', position INTEGER NOT NULL DEFAULT 0, mirror_path TEXT NOT NULL DEFAULT '')")
    db.execute("CREATE TABLE saved_samples(sample_key TEXT PRIMARY KEY, qtx_path TEXT NOT NULL, payload TEXT NOT NULL)")
    db.execute("INSERT INTO saved_qtx VALUES('a.qtx','客户A',0,'')")
    rows = [
        ('a.qtx|1', '蓝色 A', 'STD', [40.0, 2.0, -20.0]),
        ('a.qtx|2', '红色 B', 'BAT', [50.0, 30.0, 10.0]),
        ('a.qtx|3', '灰色 C', 'STD', [60.0, 1.0, 1.0]),
    ]
    for key, name, kind, lab in rows:
        payload = {
            'sample_id': key.rsplit('|', 1)[-1],
            'display_name': name,
            'kind': kind,
            'xyz_d65_10': [1.0, 2.0, 3.0],
            'lab_d65_10': lab,
            'reflectance': [10.0, 20.0, 30.0],
            'wavelengths': [400, 500, 600],
            'source_file': 'a.qtx',
            'viewing': '',
            'raw': {},
        }
        db.execute("INSERT INTO saved_samples VALUES(?,?,?)", (key, 'a.qtx', json.dumps(payload, ensure_ascii=False)))
    db.commit()
    db.close()


def _store(path: Path) -> LibraryStore:
    store = LibraryStore.__new__(LibraryStore)
    store.path = path
    store.set_access_scope(None)
    store.perf_log_path = path.with_suffix('.log')
    return store


def test_p3_2_migrates_legacy_payloads_to_light_index(tmp_path):
    path = tmp_path / 'library.sqlite3'
    _legacy_db(path)
    store = _store(path)
    store._create_schema()

    db = sqlite3.connect(path)
    rows = db.execute(
        "SELECT display_name_idx,kind_idx,lab_l_idx,family_idx,has_spectrum_idx,fluorescent_idx,index_version "
        "FROM saved_samples ORDER BY rowid"
    ).fetchall()
    meta = db.execute("SELECT value FROM schema_meta WHERE key='sample_index_version'").fetchone()
    db.close()

    assert rows == [
        ('蓝色 A', 'STD', 40.0, 'purple', 1, 0, 2),
        ('红色 B', 'BAT', 50.0, 'red', 1, 0, 2),
        ('灰色 C', 'STD', 60.0, 'neutral', 1, 0, 2),
    ]
    assert meta == ('2',)


def test_p3_2_library_page_uses_indexed_filters(tmp_path):
    path = tmp_path / 'library.sqlite3'
    _legacy_db(path)
    store = _store(path)
    store._create_schema()

    total, samples = store.library_page('客户A', search_text='蓝', kind='STD', limit=48)
    assert total == 1
    assert [sample.display_name for sample in samples] == ['蓝色 A']

    total, samples = store.library_page('客户A', family='neutral', sort_key='L', limit=48)
    assert total == 1
    assert [sample.display_name for sample in samples] == ['灰色 C']

    where, *_ = store._library_page_query_parts('客户A', None, '蓝', 'STD', 'purple', set())
    assert 'payload' not in where.lower()
    assert 'json_' not in where.lower()


def test_p3_2_corrupt_payload_is_not_exposed_by_picker_index(tmp_path):
    path = tmp_path / 'library.sqlite3'
    _legacy_db(path)
    store = _store(path)
    store._create_schema()

    db = store.connect()
    db.execute("INSERT INTO saved_samples(sample_key,qtx_path,payload) VALUES('bad','a.qtx','not-json')")
    db.commit()
    db.close()
    store.rebuild_sample_index(force=False)

    assert all(row['sample_key'] != 'bad' for row in store.sample_index())
