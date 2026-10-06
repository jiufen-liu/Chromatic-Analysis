from __future__ import annotations

import sqlite3
import json
import time
import uuid
import re
import hashlib
import math
from dataclasses import dataclass, replace
from pathlib import Path

from qtx_core.models import Sample
from qtx_core.qtx_parser import export_qtx_file
from qtx_core.colorimetry import reflectance_to_xyz_lab, munsell_hue_order_from_xyz
from .perf_monitor import profiled, configure_performance_summary
from .data_protection import library_mirror_root, app_data_root, rebase_library_mirror_paths
from .storage_v2 import (
    color_database_path, migrate_legacy_color_database, runtime_root, default_business_data_root, official_library_root,
    write_color_card_snapshot, remove_color_card_snapshot, write_workbench_snapshot,
    remove_workbench_snapshot, migrate_legacy_business_files, finalize_dg4_layout,
)


@dataclass(frozen=True)
class SavedQtx:
    path: str
    customer: str
    position: int
    mirror_path: str = ""


class LibraryStore:
    """Local formal-library index plus user-browsable customer snapshots.

    Source files opened in the workspace are never modified automatically.
    Only an explicit formal-library save writes a customer QTX snapshot.
    """

    def __init__(self) -> None:
        # DG4: the colour database is physically separated from identity and
        # audit data. Legacy databases are copied once; old files remain as a
        # rollback source and are never deleted automatically.
        migrate_legacy_color_database()
        migrate_legacy_business_files()
        self.path = color_database_path()
        root = runtime_root()
        self.perf_log_path = root / "performance.log"
        self.perf_summary_path = root / "performance_summary.json"
        configure_performance_summary(self.perf_summary_path)
        # DG-2 access context. None means unrestricted (administrator / legacy
        # compatibility mode); an explicit list, including [], enables SQL-level
        # formal-library filtering. Official access is controlled independently.
        self._access_allowed_customers: list[str] | None = None
        self._access_exact_paths: list[str] | None = None
        self._access_allow_official = True
        self._access_allowed_officials: list[str] | None = None
        self._access_formal_all = False
        # DG-3 personal ownership context. owner_user_id=0 is legacy/shared data.
        self._current_user_id = 0
        self._current_username = "共享"
        self._current_user_is_admin = True
        self._create_schema()
        try:
            rebase_library_mirror_paths(self.path)
        except Exception:
            pass
        # DG4 v2 finalisation is deliberately presentation/storage-only: SQLite
        # remains authoritative, while readable mirrors and legacy safeguards are
        # normalised once after schema/rebase have completed.
        try:
            finalize_dg4_layout(self.path)
        except Exception:
            pass


    def set_user_context(self, user_id: int, is_admin: bool = False, username: str = "") -> None:
        self._current_user_id = int(user_id or 0)
        self._current_username = str(username or ("共享" if not self._current_user_id else f"user{self._current_user_id}"))
        self._current_user_is_admin = bool(is_admin)

    def _owner_where(self, alias: str = '') -> tuple[str, tuple]:
        # DG-3.1 privacy boundary: administrator is a *system* administrator,
        # not an automatic reader of other users' private work content.
        # owner_user_id=0 remains the explicit legacy/shared compatibility bucket.
        col=(alias + '.' if alias else '') + 'owner_user_id'
        return f"({col}=0 OR {col}=?)", (int(self._current_user_id),)

    @staticmethod
    def _norm_customer(value: str) -> str:
        return str(value or '').replace('\\','/').strip('/')

    def set_exact_access_paths(self, paths: list[str] | None) -> None:
        """Use an exact whitelist of stored resource paths for DG-2.1.

        This preserves explicit child DENY rules even when a parent resource is
        allowed.  Parent/group navigation remains visible whenever at least one
        whitelisted stored path exists below it.
        """
        if paths is None:
            self._access_exact_paths=None
        else:
            self._access_exact_paths=sorted({self._norm_customer(x) for x in paths if self._norm_customer(x)},key=str.casefold)

    def set_access_scope(self, allowed_customers: list[str] | None, *, allow_official: bool = True,
                         allowed_officials: list[str] | None = None, formal_all: bool = False) -> None:
        """Apply the signed-in user's hierarchical resource scope to SQL reads.

        ``allowed_officials`` contains official-library relative prefixes such as
        ``Coloro 3500``.  ``None`` means the official root itself is allowed and
        all descendants inherit it; an explicit list allows only those branches.
        """
        self._access_exact_paths=None
        if allowed_customers is None:
            self._access_allowed_customers=None
        else:
            self._access_allowed_customers=sorted({self._norm_customer(x) for x in allowed_customers if self._norm_customer(x)},key=str.casefold)
        self._access_allow_official=bool(allow_official)
        if allowed_officials is None:
            self._access_allowed_officials=None
        else:
            self._access_allowed_officials=sorted({self._norm_customer(x).removeprefix('官方色库/').strip('/') for x in allowed_officials if self._norm_customer(x)},key=str.casefold)
        self._access_formal_all=bool(formal_all) if allowed_customers is not None else False

    def _access_condition(self, alias: str = 'q') -> tuple[str, tuple]:
        if self._access_exact_paths is not None:
            if not self._access_exact_paths:
                return '0', ()
            placeholders=','.join('?' for _ in self._access_exact_paths)
            return f"{alias}.customer IN ({placeholders})", tuple(self._access_exact_paths)
        if self._access_allowed_customers is None:
            return '', ()
        clauses=[]; params=[]
        if self._access_allow_official and self._access_allowed_officials is None:
            clauses.append(f"({alias}.customer=? OR {alias}.customer LIKE ?)")
            params.extend(('官方色库','官方色库/%'))
        elif self._access_allowed_officials:
            for prefix in self._access_allowed_officials:
                full='官方色库/'+prefix.strip('/')
                clauses.append(f"({alias}.customer=? OR {alias}.customer LIKE ?)")
                params.extend((full,full.rstrip('/')+'/%'))
        if self._access_formal_all:
            clauses.append(f"({alias}.customer<>? AND {alias}.customer NOT LIKE ?)")
            params.extend(('官方色库','官方色库/%'))
        for prefix in self._access_allowed_customers:
            if prefix=='官方色库' or prefix.startswith('官方色库/'):
                continue
            clauses.append(f"({alias}.customer=? OR {alias}.customer LIKE ?)")
            params.extend((prefix,prefix.rstrip('/')+'/%'))
        return ('('+' OR '.join(clauses)+')' if clauses else '0'), tuple(params)

    def _access_allows_customer(self, customer: str) -> bool:
        customer=self._norm_customer(customer)
        if self._access_exact_paths is not None:
            return any(customer==p or p.startswith(customer.rstrip('/')+'/') for p in self._access_exact_paths)
        if self._access_allowed_customers is None:
            return True
        if customer=='官方色库' or customer.startswith('官方色库/'):
            if not self._access_allow_official and not self._access_allowed_officials:
                return False
            if self._access_allowed_officials is None:
                return self._access_allow_official
            if customer=='官方色库':
                return bool(self._access_allowed_officials)
            relative=customer.split('/',1)[1]
            return any(relative==p or relative.startswith(p.rstrip('/')+'/') or p.startswith(relative.rstrip('/')+'/')
                       for p in self._access_allowed_officials)
        if self._access_formal_all:
            return True
        return any(customer==p or customer.startswith(p.rstrip('/')+'/') or p.startswith(customer.rstrip('/')+'/')
                   for p in self._access_allowed_customers)

    def connect(self):
        db = sqlite3.connect(self.path)
        # P3-1: tiny deterministic helpers used by SQLite-side library paging.
        # They operate only on scalar metadata; full spectral payloads are still
        # deserialised only for the page that is actually requested.
        def _casefold(value):
            return str(value or "").casefold()

        def _lower(value):
            return str(value or "").lower()

        def _colour_family(L, a, b):
            try:
                L, a, b = float(L), float(a), float(b)
                chroma = (a * a + b * b) ** 0.5
                if chroma < 4.0:
                    return "neutral"
                import math
                h = math.degrees(math.atan2(b, a)) % 360.0
                if h < 25 or h >= 330:
                    return "red"
                if h < 70:
                    return "orange"
                if h < 110:
                    return "yellow"
                if h < 165:
                    return "green"
                if h < 210:
                    return "cyan"
                if h < 270:
                    return "blue"
                return "purple"
            except Exception:
                return ""

        def _hue_index(a, b):
            """Fast sortable hue-ring key from the lightweight Lab index."""
            try:
                aa,bb=float(a),float(b)
                return (math.degrees(math.atan2(bb,aa)) % 360.0) / 3.6
            except Exception:
                return 0.0

        db.create_function("chromatic_casefold", 1, _casefold)
        db.create_function("chromatic_lower", 1, _lower)
        db.create_function("chromatic_colour_family", 3, _colour_family)
        db.create_function("chromatic_hue_index", 2, _hue_index)
        return db

    @property
    def customer_data_root(self) -> Path:
        # DG-3: application-managed mirrors no longer live in the visible Documents
        # folder. SQLite remains authoritative; these QTX files are recovery/interchange
        # mirrors kept under the application's managed local-data root.
        return library_mirror_root()

    @property
    def legacy_customer_data_root(self) -> Path:
        return default_business_data_root() / "Customers"

    def _mirror_folder_for_customer(self, customer: str) -> Path:
        parts=[p.strip() for p in str(customer or '未分类').replace('\\','/').split('/') if p.strip()]
        if parts and parts[0] == '官方色库':
            folder=official_library_root()
            parts=parts[1:]
        else:
            folder=self.customer_data_root
        for part in parts:
            folder=folder/self._safe_component(part)
        return folder

    def migrate_legacy_customer_mirrors(self) -> int:
        """Move app-generated legacy Documents mirrors into DG-3 managed storage.

        Only paths already recorded as mirror_path are touched. Original QTX source
        files are never moved. Failures leave the old file and DB row unchanged.
        """
        import shutil
        legacy=self.legacy_customer_data_root
        if not legacy.exists():
            return 0
        moved=0
        with self.connect() as db:
            rows=db.execute("SELECT path,customer,mirror_path FROM saved_qtx WHERE mirror_path<>''").fetchall()
            for source,customer,mirror in rows:
                try:
                    old=Path(str(mirror))
                    old_res=old.resolve()
                    legacy_res=legacy.resolve()
                    if old_res != legacy_res and legacy_res not in old_res.parents:
                        continue
                    if not old.exists():
                        continue
                    folder=self._mirror_folder_for_customer(str(customer or '未分类'))
                    folder.mkdir(parents=True,exist_ok=True)
                    target=folder/old.name
                    if target.exists() and target.resolve()!=old_res:
                        stem,suf=target.stem,target.suffix; n=2
                        while (folder/f"{stem}_{n}{suf}").exists(): n+=1
                        target=folder/f"{stem}_{n}{suf}"
                    shutil.copy2(old,target)
                    db.execute('UPDATE saved_qtx SET mirror_path=? WHERE path=?',(str(target),str(source)))
                    # DG4 rollback rule: preserve legacy source files.
                    moved+=1
                except Exception:
                    continue
        return moved

    @staticmethod
    def _safe_component(text: str) -> str:
        text = re.sub(r'[<>:"\\|?*]+', '_', str(text or '').strip())
        text = text.rstrip('. ')
        return text or '未分类'

    def mirror_customer_file(self, source_path: str, customer: str, samples: list[Sample], existing_mirror: str = "") -> Path | None:
        """Write a user-browsable local QTX snapshot under Documents.

        SQLite remains the fast index/cache.  The mirrored QTX gives users a
        real customer/project file tree that can be found from Windows Explorer
        without loading the whole color library into memory.
        """
        try:
            folder = self._mirror_folder_for_customer(str(customer or '未分类'))
            folder.mkdir(parents=True, exist_ok=True)
            stem = self._safe_component(Path(source_path).stem if source_path else 'Saved Colors')
            old=Path(existing_mirror) if existing_mirror else None
            if old is not None and old.parent == folder:
                target=old
            else:
                target = folder / f"{stem}.qtx"
                try: src_id=str(Path(source_path).resolve()) if source_path else stem
                except Exception: src_id=str(source_path or stem)
                if target.exists():
                    try: same_file=(str(Path(source_path).resolve()) == str(target.resolve()))
                    except Exception: same_file=False
                    if not same_file:
                        digest=hashlib.sha1(src_id.encode('utf-8','ignore')).hexdigest()[:8]
                        target=folder / f"{stem}__{digest}.qtx"
            export_qtx_file(target, samples)
            if old is not None and old != target and old.exists():
                try: old.unlink()
                except Exception: pass
            return target
        except Exception:
            return None

    SAMPLE_INDEX_VERSION = 2
    MUNSELL_INDEX_VERSION = 1

    @staticmethod
    def _colour_family_value(L, a, b) -> str:
        try:
            L, a, b = float(L), float(a), float(b)
            chroma = (a * a + b * b) ** 0.5
            if chroma < 4.0:
                return "neutral"
            import math
            h = math.degrees(math.atan2(b, a)) % 360.0
            if h < 25 or h >= 330:
                return "red"
            if h < 70:
                return "orange"
            if h < 110:
                return "yellow"
            if h < 165:
                return "green"
            if h < 210:
                return "cyan"
            if h < 270:
                return "blue"
            return "purple"
        except Exception:
            return ""

    @classmethod
    def _sample_index_values(cls, sample: Sample):
        sid="" if sample.sample_id is None else str(sample.sample_id)
        # Preserve Hotfix27 JSON semantics exactly: an explicitly empty
        # display_name stays empty for search/sort; fallback only applied when
        # the field itself is absent/None.
        name=sid if sample.display_name is None else str(sample.display_name)
        kind=str(sample.kind or "")
        try:
            L,a,b=(float(sample.lab_d65_10[0]),float(sample.lab_d65_10[1]),float(sample.lab_d65_10[2]))
            chroma=a*a+b*b
            family=cls._colour_family_value(L,a,b)
        except Exception:
            L=a=b=chroma=None
            family=""
        try:
            has_spectrum=1 if sample.has_spectrum() else 0
        except Exception:
            has_spectrum=0
        try:
            fluorescent=1 if has_spectrum and max(float(v) for v in sample.reflectance)>100.0 else 0
        except Exception:
            fluorescent=0
        return (sid,name,name.lower(),name.casefold(),kind,L,a,b,chroma,family,has_spectrum,fluorescent,cls.SAMPLE_INDEX_VERSION)

    @classmethod
    def _rebuild_sample_index_sql(cls, db, force: bool = False) -> int:
        """Rebuild lightweight scalar metadata from payload without creating Sample objects."""
        version=int(cls.SAMPLE_INDEX_VERSION)
        where="" if force else " WHERE ABS(index_version)<>?"
        params=() if force else (version,)
        before=int(db.execute("SELECT COUNT(*) FROM saved_samples"+where,params).fetchone()[0] or 0)
        if not before:
            return 0
        # Valid JSON rows: extract only scalar values. Python-backed deterministic
        # helper functions preserve Hotfix27 Unicode search/sort/family behaviour.
        valid_where=("json_valid(payload)" + ("" if force else " AND ABS(index_version)<>?"))
        valid_params=() if force else (version,)
        db.execute(
            "UPDATE saved_samples SET "
            "sample_id_idx=COALESCE(json_extract(payload,'$.sample_id'),''), "
            "display_name_idx=COALESCE(json_extract(payload,'$.display_name'),json_extract(payload,'$.sample_id'),sample_key), "
            "name_lower_idx=chromatic_lower(COALESCE(json_extract(payload,'$.display_name'),json_extract(payload,'$.sample_id'),sample_key)), "
            "name_sort_idx=chromatic_casefold(COALESCE(json_extract(payload,'$.display_name'),json_extract(payload,'$.sample_id'),sample_key)), "
            "kind_idx=COALESCE(json_extract(payload,'$.kind'),''), "
            "lab_l_idx=json_extract(payload,'$.lab_d65_10[0]'), "
            "lab_a_idx=json_extract(payload,'$.lab_d65_10[1]'), "
            "lab_b_idx=json_extract(payload,'$.lab_d65_10[2]'), "
            "chroma_sort_idx=CASE WHEN json_extract(payload,'$.lab_d65_10[1]') IS NULL OR json_extract(payload,'$.lab_d65_10[2]') IS NULL THEN NULL ELSE "
                        "(json_extract(payload,'$.lab_d65_10[1]')*json_extract(payload,'$.lab_d65_10[1]')+"
                        "json_extract(payload,'$.lab_d65_10[2]')*json_extract(payload,'$.lab_d65_10[2]')) END, "
            "family_idx=chromatic_colour_family(json_extract(payload,'$.lab_d65_10[0]'),json_extract(payload,'$.lab_d65_10[1]'),json_extract(payload,'$.lab_d65_10[2]')), "
            "has_spectrum_idx=CASE WHEN json_type(payload,'$.reflectance')='array' AND json_type(payload,'$.wavelengths')='array' "
                "AND json_array_length(json_extract(payload,'$.reflectance'))>0 "
                "AND json_array_length(json_extract(payload,'$.reflectance'))=json_array_length(json_extract(payload,'$.wavelengths')) THEN 1 ELSE 0 END, "
            "fluorescent_idx=CASE WHEN json_type(payload,'$.reflectance')='array' AND EXISTS ("
                "SELECT 1 FROM json_each(payload,'$.reflectance') WHERE CAST(value AS REAL)>100.0 LIMIT 1) THEN 1 ELSE 0 END, "
            "index_version=? WHERE "+valid_where,
            (version,)+valid_params
        )
        # Preserve legacy corrupt-row semantics: searchable by key, but full loading
        # will still skip it because _sample_from_payload remains unchanged.
        invalid_where=("NOT json_valid(payload)" + ("" if force else " AND ABS(index_version)<>?"))
        invalid_params=() if force else (version,)
        db.execute(
            "UPDATE saved_samples SET sample_id_idx='',display_name_idx=sample_key,"
            "name_lower_idx=chromatic_lower(sample_key),name_sort_idx=chromatic_casefold(sample_key),"
            "kind_idx='',lab_l_idx=NULL,lab_a_idx=NULL,lab_b_idx=NULL,chroma_sort_idx=NULL,"
            "family_idx='',has_spectrum_idx=0,fluorescent_idx=0,index_version=? WHERE "+invalid_where,
            (-version,)+invalid_params
        )
        return before

    def rebuild_sample_index(self, force: bool = True) -> int:
        """Public repair hook: rebuild the derived lightweight index from full payloads."""
        with self.connect() as db:
            changed=self._rebuild_sample_index_sql(db, force=force)
            db.execute(
                "INSERT INTO schema_meta(key,value) VALUES('sample_index_version',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(self.SAMPLE_INDEX_VERSION),)
            )
            return changed

    def _create_schema(self) -> None:
        with self.connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS saved_qtx (
                    path TEXT PRIMARY KEY,
                    customer TEXT NOT NULL DEFAULT '未分类',
                    position INTEGER NOT NULL DEFAULT 0,
                    mirror_path TEXT NOT NULL DEFAULT '',
                    data_status TEXT NOT NULL DEFAULT 'formal',
                    steward_user_id INTEGER NOT NULL DEFAULT 0,
                    created_by_user_id INTEGER NOT NULL DEFAULT 0,
                    updated_by_user_id INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL DEFAULT 0
                )"""
            )
            cols={row[1] for row in db.execute("PRAGMA table_info(saved_qtx)").fetchall()}
            saved_qtx_additions = (
                ('mirror_path', "TEXT NOT NULL DEFAULT ''"),
                ('data_status', "TEXT NOT NULL DEFAULT 'formal'"),
                ('steward_user_id', "INTEGER NOT NULL DEFAULT 0"),
                ('created_by_user_id', "INTEGER NOT NULL DEFAULT 0"),
                ('updated_by_user_id', "INTEGER NOT NULL DEFAULT 0"),
                ('created_at', "REAL NOT NULL DEFAULT 0"),
                ('updated_at', "REAL NOT NULL DEFAULT 0"),
            )
            for col_name, decl in saved_qtx_additions:
                if col_name not in cols:
                    db.execute(f"ALTER TABLE saved_qtx ADD COLUMN {col_name} {decl}")
            db.execute("""CREATE TABLE IF NOT EXISTS saved_samples (
                sample_key TEXT PRIMARY KEY, qtx_path TEXT NOT NULL, payload TEXT NOT NULL
            )""")
            # P3-2: lightweight, rebuildable scalar index beside the full payload.
            # payload remains the complete source of truth; these columns only accelerate
            # browsing/search/sort and can always be regenerated from payload.
            sample_cols={row[1] for row in db.execute("PRAGMA table_info(saved_samples)").fetchall()}
            light_columns=(
                ("sample_id_idx", "TEXT NOT NULL DEFAULT ''"),
                ("display_name_idx", "TEXT NOT NULL DEFAULT ''"),
                ("name_lower_idx", "TEXT NOT NULL DEFAULT ''"),
                ("name_sort_idx", "TEXT NOT NULL DEFAULT ''"),
                ("kind_idx", "TEXT NOT NULL DEFAULT ''"),
                ("lab_l_idx", "REAL"),
                ("lab_a_idx", "REAL"),
                ("lab_b_idx", "REAL"),
                ("chroma_sort_idx", "REAL"),
                ("family_idx", "TEXT NOT NULL DEFAULT ''"),
                ("has_spectrum_idx", "INTEGER NOT NULL DEFAULT 0"),
                ("fluorescent_idx", "INTEGER NOT NULL DEFAULT 0"),
                ("index_version", "INTEGER NOT NULL DEFAULT 0"),
                # Hotfix48: exact Munsell order is persisted separately because
                # calculating colour-science renotation for thousands of samples
                # on every sort can freeze/crash the GUI. These fields are derived
                # and rebuildable; payload remains the source of truth.
                ("munsell_neutral_idx", "INTEGER NOT NULL DEFAULT 0"),
                ("munsell_hue_idx", "REAL"),
                ("munsell_value_idx", "REAL"),
                ("munsell_chroma_idx", "REAL"),
                ("munsell_notation_idx", "TEXT NOT NULL DEFAULT ''"),
                ("munsell_index_version", "INTEGER NOT NULL DEFAULT 0"),
            )
            for col_name,col_def in light_columns:
                if col_name not in sample_cols:
                    db.execute(f"ALTER TABLE saved_samples ADD COLUMN {col_name} {col_def}")

            db.execute(
                "CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')"
            )
            # One-time / versioned rebuild for old Hotfix27 libraries.  A tiny meta
            # marker makes later startups O(1) instead of rescanning saved_samples.
            meta=db.execute("SELECT value FROM schema_meta WHERE key='sample_index_version'").fetchone()
            if not meta or str(meta[0]) != str(self.SAMPLE_INDEX_VERSION):
                # Kept inside SQLite so migration does not instantiate thousands of Sample objects.
                # Invalid legacy payloads remain addressable by sample_key but will still be
                # skipped by the existing full-payload loader, exactly as before.
                self._rebuild_sample_index_sql(db, force=False)
                db.execute(
                    "INSERT INTO schema_meta(key,value) VALUES('sample_index_version',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(self.SAMPLE_INDEX_VERSION),)
                )

            # 大色库下常用的客户过滤 / QTX→色样关联建立索引，避免每次全表扫描。
            db.execute("CREATE INDEX IF NOT EXISTS idx_saved_qtx_customer ON saved_qtx(customer)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_saved_qtx_customer_position ON saved_qtx(customer, position, path)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_saved_qtx_position ON saved_qtx(position, path)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_saved_samples_qtx_path ON saved_samples(qtx_path)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_saved_samples_kind ON saved_samples(kind_idx)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_saved_samples_family ON saved_samples(family_idx)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_saved_samples_name_lower ON saved_samples(name_lower_idx)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_saved_samples_name_sort ON saved_samples(name_sort_idx)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_saved_samples_lab_l ON saved_samples(lab_l_idx)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_saved_samples_lab_a ON saved_samples(lab_a_idx)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_saved_samples_lab_b ON saved_samples(lab_b_idx)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_saved_samples_munsell ON saved_samples(munsell_index_version,munsell_neutral_idx,munsell_hue_idx,munsell_value_idx,munsell_chroma_idx)")
            db.execute(
                """CREATE TABLE IF NOT EXISTS sample_order (
                    sample_key TEXT PRIMARY KEY,
                    position INTEGER NOT NULL
                )"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS column_settings (
                    scope TEXT PRIMARY KEY,
                    hidden_columns TEXT NOT NULL DEFAULT '[]'
                )"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS color_cards (
                    card_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    customer TEXT NOT NULL DEFAULT '未分类',
                    columns_count INTEGER NOT NULL DEFAULT 10,
                    layout_json TEXT NOT NULL DEFAULT '[]',
                    settings_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    owner_user_id INTEGER NOT NULL DEFAULT 0
                )"""
            )
            card_cols = {row[1] for row in db.execute("PRAGMA table_info(color_cards)").fetchall()}
            card_additions = (
                ("owner_user_id", "INTEGER NOT NULL DEFAULT 0"),
                ("visibility", "TEXT NOT NULL DEFAULT 'private'"),
                ("lifecycle_status", "TEXT NOT NULL DEFAULT 'draft'"),
                ("created_by_user_id", "INTEGER NOT NULL DEFAULT 0"),
                ("updated_by_user_id", "INTEGER NOT NULL DEFAULT 0"),
                ("published_at", "REAL NOT NULL DEFAULT 0"),
                ("published_by_user_id", "INTEGER NOT NULL DEFAULT 0"),
            )
            for col_name, decl in card_additions:
                if col_name not in card_cols:
                    db.execute(f"ALTER TABLE color_cards ADD COLUMN {col_name} {decl}")
            # HF140 used owner_user_id=0 as a shared compatibility bucket. DG4.1
            # no longer assumes that missing ownership means intentional sharing.
            db.execute("UPDATE color_cards SET visibility='legacy_unassigned' WHERE owner_user_id=0 AND visibility='private'")
            db.execute("UPDATE color_cards SET created_by_user_id=owner_user_id WHERE created_by_user_id=0 AND owner_user_id>0")
            db.execute("UPDATE color_cards SET updated_by_user_id=owner_user_id WHERE updated_by_user_id=0 AND owner_user_id>0")
            db.execute(
                """CREATE TABLE IF NOT EXISTS customer_groups (
                    path TEXT PRIMARY KEY,
                    created_at REAL NOT NULL DEFAULT 0
                )"""
            )
        self._create_workbench_schema()

    def list_files(self) -> list[SavedQtx]:
        access,ap=self._access_condition('q')
        where=(' WHERE '+access) if access else ''
        with self.connect() as db:
            rows = db.execute(
                "SELECT q.path, q.customer, q.position, q.mirror_path FROM saved_qtx q"+where+" ORDER BY q.position, q.path", ap
            ).fetchall()
        return [SavedQtx(*row) for row in rows]

    def customer_for_path(self, path: str, *, enforce_access: bool = True) -> str | None:
        """Resolve a saved QTX source to its customer.

        ``enforce_access=False`` is reserved for security checks on cached
        workspace/workbench payloads: it reveals only the owning customer name,
        never sample payloads, so stale embedded data can still be rejected.
        """
        access,ap=self._access_condition('q') if enforce_access else ('',())
        extra=(' AND '+access) if access else ''
        with self.connect() as db:
            row=db.execute('SELECT q.customer FROM saved_qtx q WHERE q.path=?'+extra,(str(path),)+ap).fetchone()
        return str(row[0]) if row and row[0] else None

    def formal_file_metadata(self, path: str) -> dict | None:
        with self.connect() as db:
            row=db.execute("SELECT customer,data_status,steward_user_id,created_by_user_id,updated_by_user_id,created_at,updated_at FROM saved_qtx WHERE path=?",(str(path),)).fetchone()
        if not row:return None
        return {"customer":str(row[0] or ''),"data_status":str(row[1] or 'formal'),
                "steward_user_id":int(row[2] or 0),"created_by_user_id":int(row[3] or 0),
                "updated_by_user_id":int(row[4] or 0),"created_at":float(row[5] or 0),"updated_at":float(row[6] or 0)}

    def set_formal_file_steward(self, path: str, user_id: int) -> None:
        if not self._current_user_is_admin:
            raise PermissionError('只有管理者可以调整正式色库的数据管理员')
        uid=int(user_id or 0)
        if uid<=0:raise ValueError('请选择有效用户')
        with self.connect() as db:
            if db.execute('SELECT 1 FROM saved_qtx WHERE path=?',(str(path),)).fetchone() is None:
                raise KeyError(path)
            db.execute('UPDATE saved_qtx SET steward_user_id=?,updated_by_user_id=?,updated_at=? WHERE path=?',
                       (uid,int(self._current_user_id or 0),time.time(),str(path)))

    def save_file(self, path: str, customer: str, samples: list[Sample] | None = None) -> None:
        """Create/update a formal-library asset.

        The asset belongs to the organization/customer, not to the account that
        happened to publish it. User ids are provenance/stewardship metadata.
        """
        old_mirror = ""
        now = time.time()
        with self.connect() as db:
            current = db.execute("SELECT position,mirror_path,steward_user_id,created_at FROM saved_qtx WHERE path=?", (path,)).fetchone()
            if current:
                old_mirror = str(current[1] or "")
                steward = int(current[2] or 0) or int(self._current_user_id or 0)
                db.execute("UPDATE saved_qtx SET customer=?,data_status='formal',steward_user_id=?,updated_by_user_id=?,updated_at=? WHERE path=?",
                           (customer, steward, int(self._current_user_id or 0), now, path))
            else:
                position = db.execute("SELECT COALESCE(MAX(position), -1)+1 FROM saved_qtx").fetchone()[0]
                uid = int(self._current_user_id or 0)
                db.execute(
                    "INSERT INTO saved_qtx(path,customer,position,mirror_path,data_status,steward_user_id,created_by_user_id,updated_by_user_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (path, customer, position, "", 'formal', uid, uid, uid, now, now),
                )
            if samples is not None:
                db.execute("DELETE FROM saved_samples WHERE qtx_path=?", (path,))
                for sample in samples:
                    payload = {
                        "sample_id": sample.sample_id, "display_name": sample.display_name, "kind": sample.kind,
                        "xyz_d65_10": sample.xyz_d65_10, "lab_d65_10": sample.lab_d65_10,
                        "reflectance": sample.reflectance, "wavelengths": sample.wavelengths,
                        "source_file": sample.source_file, "viewing": sample.viewing, "raw": dict(sample.raw),
                    }
                    idx=self._sample_index_values(sample)
                    db.execute(
                        "INSERT INTO saved_samples("
                        "sample_key,qtx_path,payload,sample_id_idx,display_name_idx,name_lower_idx,name_sort_idx,"
                        "kind_idx,lab_l_idx,lab_a_idx,lab_b_idx,chroma_sort_idx,family_idx,has_spectrum_idx,fluorescent_idx,index_version"
                        ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (f"{path}|{sample.sample_id}", path, json.dumps(payload, ensure_ascii=False), *idx)
                    )
        if samples is not None:
            mirror=self.mirror_customer_file(path, customer, samples, old_mirror)
            if mirror is not None:
                with self.connect() as db:
                    db.execute("UPDATE saved_qtx SET mirror_path=? WHERE path=?", (str(mirror), path))

    def sync_mirror(self, path: str) -> Path | None:
        """Synchronize one formal-library record to its Windows customer QTX."""
        with self.connect() as db:
            row=db.execute("SELECT customer,mirror_path FROM saved_qtx WHERE path=?", (path,)).fetchone()
        if not row:
            return None
        samples=self.load_samples(path)
        mirror=self.mirror_customer_file(path, str(row[0]), samples, str(row[1] or ""))
        if mirror is not None:
            with self.connect() as db:
                db.execute("UPDATE saved_qtx SET mirror_path=? WHERE path=?", (str(mirror), path))
        return mirror

    def rebuild_all_mirrors(self) -> dict:
        """Rebuild readable QTX mirrors from authoritative SQLite snapshots.

        This is an explicit maintenance operation used by DG4 Data Management;
        normal browsing never performs this full-library hydration.
        """
        with self.connect() as db:
            rows=db.execute("SELECT path FROM saved_qtx ORDER BY position,path").fetchall()
        ok=0; failed=0
        for (path,) in rows:
            try:
                mirror=self.sync_mirror(str(path))
                if mirror is not None: ok+=1
                else: failed+=1
            except Exception:
                failed+=1
        return {"total":len(rows),"rebuilt":ok,"failed":failed}

    def remove_file(self, path: str) -> None:
        with self.connect() as db:
            row=db.execute("SELECT mirror_path FROM saved_qtx WHERE path=?", (path,)).fetchone()
            db.execute("DELETE FROM saved_qtx WHERE path=?", (path,))
            db.execute("DELETE FROM saved_samples WHERE qtx_path=?", (path,))
        if row and row[0]:
            try: Path(row[0]).unlink(missing_ok=True)
            except Exception: pass

    def remove_samples(self, sample_keys: list[str]) -> None:
        """Remove selected formal samples and update their customer QTX snapshots."""
        keys = [str(k) for k in sample_keys if k]
        if not keys:
            return
        with self.connect() as db:
            placeholders=','.join('?' for _ in keys)
            paths=[r[0] for r in db.execute(f"SELECT DISTINCT qtx_path FROM saved_samples WHERE sample_key IN ({placeholders})", keys).fetchall()]
            db.executemany("DELETE FROM saved_samples WHERE sample_key=?", [(k,) for k in keys])
        for path in paths:
            try:self.sync_mirror(path)
            except Exception:pass

    def load_samples(self, path: str) -> list[Sample]:
        access,ap=self._access_condition('q')
        extra=(' AND '+access) if access else ''
        with self.connect() as db:
            rows = db.execute(
                "SELECT s.payload FROM saved_samples s JOIN saved_qtx q ON q.path=s.qtx_path WHERE s.qtx_path=?"+extra+" ORDER BY s.rowid", (path,)+ap
            ).fetchall()
        output = []
        for (payload,) in rows:
            try:
                output.append(self._sample_from_payload(payload))
            except Exception:
                continue
        return output

    @staticmethod
    def _sample_from_payload(payload: str) -> Sample:
        data = json.loads(payload)
        for key in ("xyz_d65_10", "lab_d65_10", "reflectance", "wavelengths"):
            data[key] = tuple(data[key])
        return Sample(**data)

    @profiled("store.library_contents")
    def library_contents(self, customer: str | None = None, scope: str | None = None) -> list[tuple[SavedQtx, list[Sample]]]:
        """一次 SQL 读取本地色库快照，避免过去逐个 QTX 再开一次 SQLite 的 N+1 开销。

        ``customer`` 为 None 或 ``全部客户`` 时读取全部；否则只读取该客户。
        数据来自 ``saved_samples`` 快照，不依赖原 QTX 文件仍在原位置。
        """
        clauses=[]; params=[]
        if customer and customer != "全部客户":
            clauses.append("(q.customer=? OR q.customer LIKE ?)")
            params.extend((customer, customer.rstrip('/') + '/%'))
        elif scope == '官方色库':
            clauses.append("(q.customer=? OR q.customer LIKE ?)")
            params.extend(('官方色库', '官方色库/%'))
        elif scope == '正式色库':
            clauses.append("q.customer<>? AND q.customer NOT LIKE ?")
            params.extend(('官方色库', '官方色库/%'))
        access,ap=self._access_condition('q')
        if access:
            clauses.append(access); params.extend(ap)
        where=(" WHERE "+" AND ".join(clauses)) if clauses else ""
        params=tuple(params)
        sql = (
            "SELECT q.path,q.customer,q.position,q.mirror_path,s.payload,s.rowid "
            "FROM saved_qtx q LEFT JOIN saved_samples s ON s.qtx_path=q.path" +
            where + " ORDER BY q.position,q.path,s.rowid"
        )
        with self.connect() as db:
            rows = db.execute(sql, params).fetchall()
        grouped: dict[str, tuple[SavedQtx, list[Sample]]] = {}
        order: list[str] = []
        for path, cust, pos, mirror_path, payload, _rowid in rows:
            if path not in grouped:
                grouped[path] = (SavedQtx(path, cust, pos, mirror_path or ""), [])
                order.append(path)
            if payload:
                try:
                    grouped[path][1].append(self._sample_from_payload(payload))
                except Exception:
                    # 单条损坏快照不应拖慢/阻断整个色库。
                    continue
        return [grouped[path] for path in order]

    def _library_page_query_parts(self, customer: str | None = None, scope: str | None = None,
                                  search_text: str = "", kind: str = "all", family: str = "all",
                                  hidden_keys: set[str] | None = None):
        """Build the scalar-only WHERE clause used by P3 paging.

        P3-2 reads only lightweight index columns for normal filters/sorts; the
        full payload is not inspected until the visible page is loaded.
        """
        clauses=[]; params=[]
        if customer and customer != "全部客户":
            clauses.append("(q.customer=? OR q.customer LIKE ?)")
            params.extend((customer, customer.rstrip('/') + '/%'))
        elif scope == '官方色库':
            clauses.append("(q.customer=? OR q.customer LIKE ?)")
            params.extend(('官方色库', '官方色库/%'))
        elif scope == '正式色库':
            clauses.append("q.customer<>? AND q.customer NOT LIKE ?")
            params.extend(('官方色库', '官方色库/%'))

        # P3-2: all normal browsing predicates come from scalar index columns.
        # No json_extract(payload, ...) is needed before LIMIT/OFFSET anymore.
        name_expr = "s.display_name_idx"
        name_lower = "s.name_lower_idx"
        name_sort = "s.name_sort_idx"
        kind_expr = "s.kind_idx"
        lab_l = "s.lab_l_idx"
        lab_a = "s.lab_a_idx"
        lab_b = "s.lab_b_idx"

        text=str(search_text or '').strip().lower()
        if text:
            clauses.append(f"instr({name_lower}, ?) > 0")
            params.append(text)
        if kind in {'STD','BAT'}:
            clauses.append(f"{kind_expr}=?")
            params.append(kind)
        if family and family != 'all':
            clauses.append("s.family_idx=?")
            params.append(family)
        hidden=[str(k) for k in (hidden_keys or set()) if k]
        if hidden:
            clauses.append("s.sample_key NOT IN (%s)" % ','.join('?' for _ in hidden))
            params.extend(hidden)
        access,ap=self._access_condition('q')
        if access:
            clauses.append(access); params.extend(ap)
        where=(" WHERE " + " AND ".join(clauses)) if clauses else ""
        return where, tuple(params), name_sort, lab_l, lab_a, lab_b

    def _library_page_order_sql(self, sort_key: str = 'manual', sort_desc: bool = False,
                                name_expr: str = '', lab_l: str = '', lab_a: str = '', lab_b: str = '') -> str:
        direction='DESC' if sort_desc else 'ASC'
        if sort_key == 'name':
            return f"{name_expr} {direction}, q.position ASC, q.path ASC, s.rowid ASC"
        if sort_key in {'L','a','b'}:
            expr={'L':lab_l,'a':lab_a,'b':lab_b}[sort_key]
            return f"CAST({expr} AS REAL) {direction}, q.position ASC, q.path ASC, s.rowid ASC"
        if sort_key == 'C':
            return (f"s.chroma_sort_idx {direction}, q.position ASC, q.path ASC, s.rowid ASC")
        if sort_key == 'h_fast':
            # Hotfix49: instant visual hue order from the lightweight Lab index.
            # Exact Munsell notation is still calculated on demand in details.
            return (
                "CASE WHEN COALESCE(s.chroma_sort_idx,0)<9.0 THEN 1 ELSE 0 END ASC, "
                f"CASE WHEN COALESCE(s.chroma_sort_idx,0)>=9.0 THEN chromatic_hue_index(s.lab_a_idx,s.lab_b_idx) END {direction}, "
                "s.lab_l_idx DESC, s.chroma_sort_idx DESC, "
                f"{name_expr} ASC, q.position ASC, q.path ASC, s.rowid ASC"
            )
        if sort_key == 'h':
            # Preserve the legacy Munsell ordering: chromatic colours first by
            # Munsell hue, then Value/Chroma; neutral colours stay last and are
            # ordered light-to-dark regardless of hue direction.
            return (
                "CASE WHEN s.munsell_neutral_idx<>0 THEN 1 ELSE 0 END ASC, "
                f"CASE WHEN s.munsell_neutral_idx=0 THEN s.munsell_hue_idx END {direction}, "
                "s.munsell_value_idx DESC, s.munsell_chroma_idx DESC, "
                f"{name_expr} ASC, q.position ASC, q.path ASC, s.rowid ASC"
            )
        # 'manual' in the formal library is the stable QTX/source order. The
        # old Python sort is stable and all library-only keys have the same
        # fallback rank, so this exactly preserves that behaviour.
        return "q.position ASC, q.path ASC, s.rowid ASC"

    def _munsell_scope_where(self, customer: str | None = None, scope: str | None = None):
        clauses=[]; params=[]
        if customer and customer != "全部客户":
            clauses.append("(q.customer=? OR q.customer LIKE ?)")
            params.extend((customer, customer.rstrip('/') + '/%'))
        elif scope == '官方色库':
            clauses.append("(q.customer=? OR q.customer LIKE ?)")
            params.extend(('官方色库', '官方色库/%'))
        elif scope == '正式色库':
            clauses.append("q.customer<>? AND q.customer NOT LIKE ?")
            params.extend(('官方色库', '官方色库/%'))
        access,ap=self._access_condition('q')
        if access:
            clauses.append(access); params.extend(ap)
        where=(" WHERE "+" AND ".join(clauses)) if clauses else ""
        return where, tuple(params)

    def munsell_missing_count(self, customer: str | None = None, scope: str | None = None) -> int:
        """Count rows whose exact Munsell sort key has not been persisted yet."""
        where,params=self._munsell_scope_where(customer,scope)
        extra=(" AND " if where else " WHERE ")+"s.munsell_index_version<>?"
        sql=("SELECT COUNT(*) FROM saved_samples s JOIN saved_qtx q ON q.path=s.qtx_path"+where+extra)
        with self.connect() as db:
            return int(db.execute(sql,params+(int(self.MUNSELL_INDEX_VERSION),)).fetchone()[0] or 0)

    @classmethod
    def _munsell_values_from_payload(cls, payload: str):
        """Return persisted legacy-compatible Munsell ordering values.

        This reproduces MainWindow._perceptual_hue_info semantics without keeping
        thousands of complete Sample objects in memory.
        """
        try:
            data=json.loads(payload)
            lab=data.get('lab_d65_10') or ()
            L,a,b=(float(lab[0]),float(lab[1]),float(lab[2]))
            C=math.hypot(a,b)
        except Exception:
            return (1,None,0.0,0.0,'')
        if C < 3.0:
            return (1,None,L,C,'Munsell 中性色 / 低彩度')
        try:
            refl=tuple(float(v) for v in (data.get('reflectance') or ()))
            waves=tuple(int(float(v)) for v in (data.get('wavelengths') or ()))
            if len(refl)<2 or len(refl)!=len(waves):
                raise ValueError('missing spectrum')
            xyz,_=reflectance_to_xyz_lab(refl,'C',waves,2)
            idx,notation,value,chroma=munsell_hue_order_from_xyz(xyz)
            neutral=(not math.isfinite(float(idx))) or float(chroma)<1.5
            return (1 if neutral else 0, None if neutral else float(idx), float(value), float(chroma), str(notation or ''))
        except Exception:
            # Match the existing UI fallback when Munsell renotation is outside
            # the interpolation domain: use CIELAB h° only for that sample.
            h=math.degrees(math.atan2(b,a))%360.0
            return (0,h/3.6,L,C,f'Munsell 回退 / Lab h° {h:.1f}')

    @profiled('store.build_munsell_index')
    def build_munsell_index(self, customer: str | None = None, scope: str | None = None, batch_size: int = 64) -> int:
        """Build exact Munsell sort keys incrementally and persist them.

        Intended to run on a worker thread. Only one payload is decoded at a
        time and updates are committed in small batches, preventing the previous
        all-library materialisation/crash while making subsequent sorts pure SQL.
        """
        version=int(self.MUNSELL_INDEX_VERSION)
        where,params=self._munsell_scope_where(customer,scope)
        extra=(" AND " if where else " WHERE ")+"s.munsell_index_version<>?"
        sql=("SELECT s.sample_key,s.payload FROM saved_samples s JOIN saved_qtx q ON q.path=s.qtx_path"+
             where+extra+" ORDER BY q.position,q.path,s.rowid")
        changed=0
        with self.connect() as db:
            cur=db.execute(sql,params+(version,))
            while True:
                rows=cur.fetchmany(max(8,int(batch_size)))
                if not rows: break
                updates=[]
                for key,payload in rows:
                    neutral,hue,value,chroma,notation=self._munsell_values_from_payload(payload)
                    updates.append((neutral,hue,value,chroma,notation,version,str(key)))
                db.executemany(
                    "UPDATE saved_samples SET munsell_neutral_idx=?,munsell_hue_idx=?,munsell_value_idx=?,"
                    "munsell_chroma_idx=?,munsell_notation_idx=?,munsell_index_version=? WHERE sample_key=?",
                    updates)
                db.commit(); changed+=len(updates)
                # Yield between batches so a CPU-heavy colour-science conversion
                # cannot monopolise the process on office-class machines.
                time.sleep(0.002)
        return changed

    @staticmethod
    def _sample_from_index_row(row) -> Sample | None:
        """Build a display-only Sample from P3 scalar index columns.

        P3-3 deliberately keeps spectrum/XYZ/raw payload on disk until a feature
        explicitly requests them.  ``raw`` carries only internal flags required to
        preserve card presentation (for example the fluorescent folded corner).
        """
        try:
            key,qtx_path,sample_id,display_name,kind,L,a,b,has_spectrum,fluorescent=row
            if None in (L,a,b):
                return None
            try: canonical=str(Path(qtx_path).resolve())
            except Exception: canonical=str(qtx_path)
            sid=str(sample_id or '')
            name=str(display_name if display_name is not None else sid or key)
            raw={
                '__P3_LIGHTWEIGHT__':'1',
                '__HAS_SPECTRUM_INDEX':'1' if has_spectrum else '0',
                '__FLUORESCENT_INDEX':'1' if fluorescent else '0',
            }
            return Sample(
                sample_id=sid, display_name=name, kind=str(kind or ''),
                xyz_d65_10=(0.0,0.0,0.0),
                lab_d65_10=(float(L),float(a),float(b)),
                reflectance=tuple(), wavelengths=tuple(),
                source_file=canonical, viewing='', raw=raw
            )
        except Exception:
            return None

    @profiled("store.library_page")
    def library_page(self, customer: str | None = None, scope: str | None = None, *,
                     search_text: str = "", kind: str = "all", family: str = "all",
                     sort_key: str = "manual", sort_desc: bool = False,
                     offset: int = 0, limit: int = 48, hidden_keys: set[str] | None = None,
                     lightweight: bool = True) -> tuple[int, list[Sample]]:
        """Return ``(total, current_page_samples)`` without materialising the whole library.

        P3-3 default: the page query reads only rebuildable scalar index columns,
        so browsing/search/paging does not fetch the JSON payload or spectrum at
        all.  Callers that genuinely need an alternate-illuminant preview may set
        ``lightweight=False``; even then only the visible page payloads are read.
        """
        where,params,name_expr,lab_l,lab_a,lab_b=self._library_page_query_parts(
            customer,scope,search_text,kind,family,hidden_keys)
        order_sql=self._library_page_order_sql(sort_key,sort_desc,name_expr,lab_l,lab_a,lab_b)
        base=" FROM saved_samples s JOIN saved_qtx q ON q.path=s.qtx_path"
        offset=max(0,int(offset)); limit=max(1,int(limit))
        with self.connect() as db:
            light_where=(where + (" AND " if where else " WHERE ") + "s.index_version>0") if lightweight else where
            total=int(db.execute("SELECT COUNT(*)"+base+light_where,params).fetchone()[0] or 0)
            if lightweight:
                # Keep ORDER/LIMIT in a separate statement so WHERE composition stays
                # readable and no payload column can accidentally slip back in.
                rows=db.execute(
                    "SELECT s.sample_key,s.qtx_path,s.sample_id_idx,s.display_name_idx,s.kind_idx,"
                    "s.lab_l_idx,s.lab_a_idx,s.lab_b_idx,s.has_spectrum_idx,s.fluorescent_idx"+base+light_where+
                    " ORDER BY "+order_sql+" LIMIT ? OFFSET ?", params+(limit,offset)
                ).fetchall()
            else:
                rows=db.execute(
                    "SELECT s.sample_key,s.qtx_path,s.payload"+base+where+
                    " ORDER BY "+order_sql+" LIMIT ? OFFSET ?", params+(limit,offset)
                ).fetchall()
        out=[]
        if lightweight:
            for row in rows:
                sample=self._sample_from_index_row(row)
                if sample is not None: out.append(sample)
        else:
            for _key,qtx_path,payload in rows:
                try:
                    sample=self._sample_from_payload(payload)
                    try: canonical=str(Path(qtx_path).resolve())
                    except Exception: canonical=str(qtx_path)
                    out.append(replace(sample,source_file=canonical))
                except Exception:
                    continue
        return total,out

    @profiled("store.library_query_keys")
    def library_query_keys(self, customer: str | None = None, scope: str | None = None, *,
                           search_text: str = "", kind: str = "all", family: str = "all",
                           sort_key: str = "manual", sort_desc: bool = False,
                           hidden_keys: set[str] | None = None) -> list[str]:
        """Return ordered keys for selection/copy without loading any spectral payloads."""
        where,params,name_expr,lab_l,lab_a,lab_b=self._library_page_query_parts(
            customer,scope,search_text,kind,family,hidden_keys)
        order_sql=self._library_page_order_sql(sort_key,sort_desc,name_expr,lab_l,lab_a,lab_b)
        sql=("SELECT s.sample_key FROM saved_samples s JOIN saved_qtx q ON q.path=s.qtx_path"+
             where+" ORDER BY "+order_sql)
        with self.connect() as db:
            rows=db.execute(sql,params).fetchall()
        return [str(row[0]) for row in rows if row and row[0]]

    def load_samples_by_keys(self, sample_keys: list[str] | set[str]) -> list[Sample]:
        """Load only explicitly requested formal-library samples, preserving key order."""
        keys=[str(k) for k in sample_keys if k]
        if not keys:return []
        found={}
        # Keep well below SQLite's host-parameter limit on older Windows builds.
        with self.connect() as db:
            for start in range(0,len(keys),400):
                chunk=keys[start:start+400]
                placeholders=','.join('?' for _ in chunk)
                access,ap=self._access_condition('q')
                extra=(' AND '+access) if access else ''
                rows=db.execute(
                    f"SELECT s.sample_key,s.qtx_path,s.payload FROM saved_samples s JOIN saved_qtx q ON q.path=s.qtx_path WHERE s.sample_key IN ({placeholders})"+extra,
                    tuple(chunk)+ap).fetchall()
                for key,qtx_path,payload in rows:
                    try:
                        sample=self._sample_from_payload(payload)
                        try:canonical=str(Path(qtx_path).resolve())
                        except Exception:canonical=str(qtx_path)
                        found[str(key)]=replace(sample,source_file=canonical)
                    except Exception:
                        continue
        return [found[k] for k in keys if k in found]

    @profiled("store.load_index_samples_by_keys")
    def load_index_samples_by_keys(self, sample_keys: list[str] | set[str]) -> list[Sample]:
        """Build lightweight Sample objects from the scalar index only.

        No JSON payload, spectrum, XYZ block or measurement metadata is read.
        Use ``load_samples_by_keys`` only when a downstream operation explicitly
        needs those heavy fields.
        """
        keys=[str(k) for k in sample_keys if k]
        if not keys:
            return []
        found={}
        with self.connect() as db:
            for start in range(0,len(keys),400):
                chunk=keys[start:start+400]
                placeholders=','.join('?' for _ in chunk)
                access,ap=self._access_condition('q')
                extra=(' AND '+access) if access else ''
                rows=db.execute(
                    f"SELECT s.sample_key,s.qtx_path,s.sample_id_idx,s.display_name_idx,s.kind_idx,"
                    f"s.lab_l_idx,s.lab_a_idx,s.lab_b_idx,s.has_spectrum_idx,s.fluorescent_idx FROM saved_samples s JOIN saved_qtx q ON q.path=s.qtx_path "
                    f"WHERE s.sample_key IN ({placeholders}) AND s.index_version>0"+extra,
                    tuple(chunk)+ap).fetchall()
                for row in rows:
                    sample=self._sample_from_index_row(row)
                    if sample is not None:
                        found[str(row[0])]=sample
        return [found[k] for k in keys if k in found]



    @profiled("store.customer_counts")
    def customer_counts(self) -> dict[str, int]:
        """Return only data-scope-visible customer counts."""
        access,ap=self._access_condition('q'); where=(' WHERE '+access) if access else ''
        with self.connect() as db:
            rows=db.execute(
                "SELECT q.customer, COUNT(s.sample_key) "
                "FROM saved_qtx q LEFT JOIN saved_samples s ON s.qtx_path=q.path"+where+
                " GROUP BY q.customer", ap
            ).fetchall()
        return {str(customer): int(count or 0) for customer,count in rows if customer}

    @profiled("store.library_navigation_snapshot")
    def library_navigation_snapshot(self):
        """Return counts, empty groups and QTX rows using one SQLite connection.

        This is used by the formal-library sidebar so refreshing the navigation
        tree does not reopen the database three times or materialize any sample
        payload/spectrum.
        """
        access,ap=self._access_condition('q'); where=(' WHERE '+access) if access else ''
        with self.connect() as db:
            count_rows = db.execute(
                "SELECT q.customer, COUNT(s.sample_key) "
                "FROM saved_qtx q LEFT JOIN saved_samples s ON s.qtx_path=q.path"+where+
                " GROUP BY q.customer", ap
            ).fetchall()
            group_rows = db.execute("SELECT path FROM customer_groups ORDER BY path").fetchall()
            file_rows = db.execute(
                "SELECT q.path, q.customer, q.position, q.mirror_path FROM saved_qtx q"+where+" ORDER BY q.position, q.path", ap
            ).fetchall()
        counts = {str(customer): int(count or 0) for customer, count in count_rows if customer}
        groups = [str(row[0]) for row in group_rows if row and row[0] and self._access_allows_customer(str(row[0]))]
        files = [SavedQtx(*row) for row in file_rows]
        return counts, groups, files

    def customers(self) -> list[str]:
        """Return all data-scope-visible customer/category paths."""
        access,ap=self._access_condition('q'); where=(' WHERE '+access) if access else ''
        with self.connect() as db:
            qtx = [row[0] for row in db.execute("SELECT DISTINCT q.customer FROM saved_qtx q"+where,ap).fetchall()]
            groups = [row[0] for row in db.execute("SELECT path FROM customer_groups").fetchall()]
        return sorted({x for x in qtx + groups if x and self._access_allows_customer(str(x))}, key=str.casefold)

    def save_customer_group(self, path: str) -> str:
        path = "/".join(part.strip() for part in str(path).replace("\\", "/").split("/") if part.strip())
        if not path:
            raise ValueError("客户/子客户名称不能为空")
        # Save every parent too, so a folder-like tree can contain empty branches.
        parts = path.split("/")
        with self.connect() as db:
            for i in range(1, len(parts)+1):
                p = "/".join(parts[:i])
                db.execute("INSERT OR IGNORE INTO customer_groups(path,created_at) VALUES(?,?)", (p, time.time()))
        return path

    def remove_customer_group(self, path: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM customer_groups WHERE path=? OR path LIKE ?", (path, path + "/%"))

    def list_customer_groups(self) -> list[str]:
        with self.connect() as db:
            rows = db.execute("SELECT path FROM customer_groups ORDER BY path").fetchall()
        return [row[0] for row in rows if row and row[0] and self._access_allows_customer(str(row[0]))]


    @profiled("store.sample_index")
    def sample_index(self) -> list[dict]:
        """Lightweight index for pickers/search without reading full spectral payloads."""
        access,ap=self._access_condition('q')
        access_sql=(' AND '+access) if access else ''
        sql=("SELECT s.sample_key,s.qtx_path,q.customer,s.display_name_idx,s.sample_id_idx,s.kind_idx,"
             "s.lab_l_idx,s.lab_a_idx,s.lab_b_idx,s.family_idx,s.has_spectrum_idx,s.fluorescent_idx "
             "FROM saved_samples s JOIN saved_qtx q ON q.path=s.qtx_path "
             "WHERE s.index_version>0"+access_sql+" ORDER BY q.position,s.rowid")
        out=[]
        with self.connect() as db:
            rows=db.execute(sql,ap).fetchall()
        for sample_key,qtx_path,customer,display_name,sample_id,kind,L,a,b,family,has_spectrum,fluorescent in rows:
            out.append({"sample_key":str(sample_key),"qtx_path":str(qtx_path),"customer":str(customer),
                        "display_name":str(display_name or sample_id or sample_key),
                        "sample_id":str(sample_id or ''),"kind":str(kind or ''),
                        "lab_d65_10":(L,a,b) if None not in (L,a,b) else None,
                        "family":str(family or ''),"has_spectrum":bool(has_spectrum),
                        "fluorescent":bool(fluorescent)})
        return out

    @profiled("store.customer_index_samples")
    def customer_index_samples(self, customer: str) -> list[Sample]:
        """Return one customer/subtree as lightweight samples only.

        Used by palette/source browsers so merely opening a customer never
        deserialises its reflectance arrays.
        """
        customer=str(customer or '').strip()
        if not customer:
            return []
        access,ap=self._access_condition('q'); access_sql=(' AND '+access) if access else ''
        sql=("SELECT s.sample_key,s.qtx_path,s.sample_id_idx,s.display_name_idx,s.kind_idx,"
             "s.lab_l_idx,s.lab_a_idx,s.lab_b_idx,s.has_spectrum_idx,s.fluorescent_idx "
             "FROM saved_samples s JOIN saved_qtx q ON q.path=s.qtx_path "
             "WHERE s.index_version>0 AND (q.customer=? OR q.customer LIKE ?)"+access_sql+
             " ORDER BY q.position,q.path,s.rowid")
        with self.connect() as db:
            rows=db.execute(sql,(customer,customer.rstrip('/')+'/%')+ap).fetchall()
        out=[]
        for row in rows:
            sample=self._sample_from_index_row(row)
            if sample is not None: out.append(sample)
        return out

    def load_sample_by_key(self, sample_key: str) -> Sample | None:
        access,ap=self._access_condition('q'); extra=(' AND '+access) if access else ''
        with self.connect() as db:
            row=db.execute("SELECT s.qtx_path,s.payload FROM saved_samples s JOIN saved_qtx q ON q.path=s.qtx_path WHERE s.sample_key=?"+extra,(sample_key,)+ap).fetchone()
        if not row:return None
        try:
            sample=self._sample_from_payload(row[1])
            try:canonical=str(Path(row[0]).resolve())
            except Exception:canonical=str(row[0])
            return replace(sample,source_file=canonical)
        except Exception:return None

    def load_samples_by_sample_ids(self, sample_ids: list[str] | set[str]) -> list[Sample]:
        """Hydrate only rows whose indexed sample id is explicitly requested.

        Used for repairing legacy colour-card keys after a source path changed.
        It avoids the old recovery path that decoded the entire formal library.
        """
        ids=[str(x) for x in sample_ids if x]
        if not ids:return []
        found=[]
        with self.connect() as db:
            for start in range(0,len(ids),400):
                chunk=ids[start:start+400]
                placeholders=','.join('?' for _ in chunk)
                access,ap=self._access_condition('q'); extra=(' AND '+access) if access else ''
                rows=db.execute(
                    f"SELECT s.qtx_path,s.payload FROM saved_samples s JOIN saved_qtx q ON q.path=s.qtx_path WHERE s.sample_id_idx IN ({placeholders})"+extra,
                    tuple(chunk)+ap).fetchall()
                for qtx_path,payload in rows:
                    try:
                        sample=self._sample_from_payload(payload)
                        try:canonical=str(Path(qtx_path).resolve())
                        except Exception:canonical=str(qtx_path)
                        found.append(replace(sample,source_file=canonical))
                    except Exception:
                        continue
        return found

    def has_samples(self) -> bool:
        """Cheap existence check inside the signed-in user's data scope."""
        access,ap=self._access_condition('q'); where=(' WHERE '+access) if access else ''
        with self.connect() as db:
            return db.execute("SELECT 1 FROM saved_samples s JOIN saved_qtx q ON q.path=s.qtx_path"+where+" LIMIT 1",ap).fetchone() is not None

    def update_sample_snapshot(self, sample_key: str, sample: Sample) -> bool:
        """Persist edits to an already-saved sample snapshot without touching the source file."""
        payload = {
            "sample_id": sample.sample_id,
            "display_name": sample.display_name,
            "kind": sample.kind,
            "xyz_d65_10": sample.xyz_d65_10,
            "lab_d65_10": sample.lab_d65_10,
            "reflectance": sample.reflectance,
            "wavelengths": sample.wavelengths,
            "source_file": sample.source_file,
            "viewing": sample.viewing,
            "raw": dict(sample.raw),
        }
        qtx_path=None
        with self.connect() as db:
            row=db.execute("SELECT qtx_path FROM saved_samples WHERE sample_key=?", (str(sample_key),)).fetchone()
            qtx_path=row[0] if row else None
            idx=self._sample_index_values(sample)
            cur=db.execute(
                "UPDATE saved_samples SET payload=?,sample_id_idx=?,display_name_idx=?,name_lower_idx=?,name_sort_idx=?,"
                "kind_idx=?,lab_l_idx=?,lab_a_idx=?,lab_b_idx=?,chroma_sort_idx=?,family_idx=?,has_spectrum_idx=?,fluorescent_idx=?,index_version=?,"
                "munsell_neutral_idx=0,munsell_hue_idx=NULL,munsell_value_idx=NULL,munsell_chroma_idx=NULL,munsell_notation_idx='',munsell_index_version=0 "
                "WHERE sample_key=?",
                (json.dumps(payload, ensure_ascii=False), *idx, str(sample_key)))
            changed=bool(cur.rowcount)
        if changed and qtx_path:
            try:self.sync_mirror(qtx_path)
            except Exception:pass
        return changed

    def load_order(self) -> dict[str, int]:
        with self.connect() as db:
            return dict(db.execute("SELECT sample_key, position FROM sample_order").fetchall())

    def save_order(self, ordered_keys: list[str]) -> None:
        with self.connect() as db:
            db.executemany(
                "INSERT INTO sample_order(sample_key, position) VALUES(?,?) "
                "ON CONFLICT(sample_key) DO UPDATE SET position=excluded.position",
                [(key, position) for position, key in enumerate(ordered_keys)],
            )

    # ---------------- 色卡方案 ----------------
    def _color_card_where(self, alias: str = '') -> tuple[str, tuple]:
        prefix=(alias+'.') if alias else ''
        owner=prefix+'owner_user_id'; visibility=prefix+'visibility'
        # Private content stays private even for system administrators. Shared
        # organization schemes are readable by all signed-in users. Legacy owner
        # 0 stays visible until an administrator explicitly assigns it.
        return f"({owner}=? OR {visibility}='organization' OR {visibility}='legacy_unassigned' OR {owner}=0)", (int(self._current_user_id),)

    def list_color_cards(self) -> list[dict]:
        where_expr, params = self._color_card_where()
        where = (' WHERE '+where_expr) if where_expr else ''
        with self.connect() as db:
            rows = db.execute(
                "SELECT card_id,name,customer,columns_count,layout_json,settings_json,"
                "created_at,updated_at,owner_user_id,visibility,lifecycle_status,created_by_user_id,updated_by_user_id,published_at,published_by_user_id "
                "FROM color_cards"+where+" ORDER BY updated_at DESC", params).fetchall()
        result=[]
        for row in rows:
            result.append({
                "card_id":row[0],"name":row[1],"customer":row[2],"columns_count":row[3],
                "layout":json.loads(row[4] or "[]"),"settings":json.loads(row[5] or "{}"),
                "created_at":row[6],"updated_at":row[7],"owner_user_id":int(row[8] or 0),
                "visibility":str(row[9] or ('legacy_unassigned' if int(row[8] or 0)<=0 else 'private')),
                "lifecycle_status":str(row[10] or 'draft'),"created_by_user_id":int(row[11] or 0),
                "updated_by_user_id":int(row[12] or 0),"published_at":float(row[13] or 0),
                "published_by_user_id":int(row[14] or 0),
            })
        return result

    def get_color_card(self, card_id: str) -> dict | None:
        return next((c for c in self.list_color_cards() if str(c.get('card_id'))==str(card_id)),None)

    def save_color_card(self, card: dict) -> dict:
        now=time.time(); card=dict(card); card.setdefault("card_id",str(uuid.uuid4())); card.setdefault("created_at",now); card["updated_at"]=now
        with self.connect() as db:
            existing=db.execute('SELECT owner_user_id,visibility,lifecycle_status,created_by_user_id,published_at,published_by_user_id FROM color_cards WHERE card_id=?',(card["card_id"],)).fetchone()
            if existing is not None:
                owner=int(existing[0] or 0)
                if owner != self._current_user_id:
                    if owner<=0:
                        raise PermissionError('历史未归属方案请先由管理员明确指定所有者后再编辑')
                    raise PermissionError('该色卡方案属于其他用户；共享只扩大可见范围，不转移所有权或编辑权')
                visibility=str(existing[1] or 'private'); lifecycle=str(existing[2] or 'draft')
                created_by=int(existing[3] or owner); published_at=float(existing[4] or 0); published_by=int(existing[5] or 0)
            else:
                owner=int(self._current_user_id or 0)
                visibility='private' if owner>0 else 'legacy_unassigned'
                lifecycle='draft'; created_by=owner; published_at=0.0; published_by=0
            db.execute(
                """INSERT INTO color_cards(card_id,name,customer,columns_count,layout_json,settings_json,created_at,updated_at,owner_user_id,visibility,lifecycle_status,created_by_user_id,updated_by_user_id,published_at,published_by_user_id)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(card_id) DO UPDATE SET name=excluded.name,customer=excluded.customer,columns_count=excluded.columns_count,
                   layout_json=excluded.layout_json,settings_json=excluded.settings_json,updated_at=excluded.updated_at,updated_by_user_id=excluded.updated_by_user_id""",
                (card["card_id"],card["name"],card.get("customer","未分类"),int(card.get("columns_count",10)),
                 json.dumps(card.get("layout",[]),ensure_ascii=False),json.dumps(card.get("settings",{}),ensure_ascii=False),
                 card["created_at"],card["updated_at"],owner,visibility,lifecycle,created_by,int(self._current_user_id or 0),published_at,published_by))
        card.update({'owner_user_id':owner,'visibility':visibility,'lifecycle_status':lifecycle,'created_by_user_id':created_by,
                     'updated_by_user_id':int(self._current_user_id or 0),'published_at':published_at,'published_by_user_id':published_by})
        try:write_color_card_snapshot(card,owner,self._current_username if owner==self._current_user_id else f'user{owner}')
        except Exception:pass
        return card

    def set_color_card_visibility(self, card_id: str, visibility: str, owner_username: str = '') -> dict:
        visibility=str(visibility or 'private')
        if visibility not in {'private','organization'}:raise ValueError('不支持的可见范围')
        with self.connect() as db:
            row=db.execute('SELECT owner_user_id FROM color_cards WHERE card_id=?',(card_id,)).fetchone()
            if row is None:raise KeyError(card_id)
            owner=int(row[0] or 0)
            if owner<=0:raise PermissionError('历史未归属方案必须先指定所有者')
            if owner!=self._current_user_id and not self._current_user_is_admin:raise PermissionError('只能管理自己的方案共享范围')
            db.execute("UPDATE color_cards SET visibility=?,updated_at=?,updated_by_user_id=? WHERE card_id=?",
                       (visibility,time.time(),int(self._current_user_id or 0),card_id))
        card=self.get_color_card(card_id)
        if card:
            try:write_color_card_snapshot(card,owner,owner_username or (self._current_username if owner==self._current_user_id else f'user{owner}'))
            except Exception:pass
        return card or {}

    def set_color_card_lifecycle(self, card_id: str, lifecycle_status: str, owner_username: str = '') -> dict:
        status=str(lifecycle_status or 'draft')
        if status not in {'draft','published'}:raise ValueError('不支持的方案状态')
        if status=='published' and not self._current_user_is_admin:raise PermissionError('只有管理者可以发布正式色卡方案')
        now=time.time()
        with self.connect() as db:
            row=db.execute('SELECT owner_user_id FROM color_cards WHERE card_id=?',(card_id,)).fetchone()
            if row is None:raise KeyError(card_id)
            owner=int(row[0] or 0)
            if owner<=0:raise PermissionError('历史未归属方案必须先指定所有者')
            if owner!=self._current_user_id and not self._current_user_is_admin:raise PermissionError('只能管理自己的方案')
            visibility='organization' if status=='published' else None
            if status=='published':
                db.execute("UPDATE color_cards SET lifecycle_status='published',visibility='organization',published_at=?,published_by_user_id=?,updated_at=?,updated_by_user_id=? WHERE card_id=?",
                           (now,int(self._current_user_id or 0),now,int(self._current_user_id or 0),card_id))
            else:
                db.execute("UPDATE color_cards SET lifecycle_status='draft',published_at=0,published_by_user_id=0,updated_at=?,updated_by_user_id=? WHERE card_id=?",
                           (now,int(self._current_user_id or 0),card_id))
        card=self.get_color_card(card_id)
        if card:
            try:write_color_card_snapshot(card,owner,owner_username or (self._current_username if owner==self._current_user_id else f'user{owner}'))
            except Exception:pass
        return card or {}

    def transfer_color_card_owner(self, card_id: str, target_user_id: int, target_username: str = '') -> dict:
        if not self._current_user_is_admin:raise PermissionError('只有管理者可以转移方案所有者')
        target=int(target_user_id or 0)
        if target<=0:raise ValueError('请选择有效用户')
        with self.connect() as db:
            row=db.execute('SELECT owner_user_id,visibility FROM color_cards WHERE card_id=?',(card_id,)).fetchone()
            if row is None:raise KeyError(card_id)
            old_owner=int(row[0] or 0); vis=str(row[1] or 'private')
            if old_owner<=0 and vis=='legacy_unassigned':vis='private'
            db.execute('UPDATE color_cards SET owner_user_id=?,visibility=?,updated_at=?,updated_by_user_id=? WHERE card_id=?',
                       (target,vis,time.time(),int(self._current_user_id or 0),card_id))
        try:remove_color_card_snapshot(card_id)
        except Exception:pass
        # The current user's visibility query may not return a private card moved
        # to another user. Read a metadata-safe copy directly for mirror rebuild.
        with self.connect() as db:
            row=db.execute("SELECT card_id,name,customer,columns_count,layout_json,settings_json,created_at,updated_at,owner_user_id,visibility,lifecycle_status,created_by_user_id,updated_by_user_id,published_at,published_by_user_id FROM color_cards WHERE card_id=?",(card_id,)).fetchone()
        if row:
            card={'card_id':row[0],'name':row[1],'customer':row[2],'columns_count':row[3],'layout':json.loads(row[4] or '[]'),'settings':json.loads(row[5] or '{}'),
                  'created_at':row[6],'updated_at':row[7],'owner_user_id':int(row[8] or 0),'visibility':row[9],'lifecycle_status':row[10],
                  'created_by_user_id':int(row[11] or 0),'updated_by_user_id':int(row[12] or 0),'published_at':float(row[13] or 0),'published_by_user_id':int(row[14] or 0)}
            try:write_color_card_snapshot(card,target,target_username or f'user{target}')
            except Exception:pass
            return card
        return {}

    def duplicate_color_card_to_current_user(self, card_id: str, new_name: str = '') -> dict:
        source=self.get_color_card(card_id)
        if not source:raise KeyError(card_id)
        copy=dict(source); copy['card_id']=str(uuid.uuid4()); copy['name']=str(new_name or (str(source.get('name','色卡方案'))+' 副本'))
        copy['created_at']=time.time(); copy['owner_user_id']=int(self._current_user_id or 0)
        for k in ('visibility','lifecycle_status','created_by_user_id','updated_by_user_id','published_at','published_by_user_id'):copy.pop(k,None)
        return self.save_color_card(copy)

    def remove_color_card(self, card_id: str) -> None:
        removed=False
        with self.connect() as db:
            row=db.execute("SELECT owner_user_id FROM color_cards WHERE card_id=?",(card_id,)).fetchone()
            if row is None:return
            owner=int(row[0] or 0)
            allowed=(owner==self._current_user_id) or (owner<=0 and self._current_user_is_admin)
            if not allowed:raise PermissionError('只能删除自己的方案；管理者可处理历史未归属方案')
            db.execute("DELETE FROM color_cards WHERE card_id=?",(card_id,)); removed=True
        if removed:
            try:remove_color_card_snapshot(card_id)
            except Exception:pass

    # ---------------- DG-3.1 private ownership administration ----------------
    def private_data_summary(self) -> list[dict]:
        """Return counts by owner without exposing private payload/content."""
        self._create_workbench_schema()
        with self.connect() as db:
            wb={int(uid or 0):int(cnt) for uid,cnt in db.execute(
                "SELECT owner_user_id,COUNT(*) FROM workbenches GROUP BY owner_user_id").fetchall()}
            cards={int(uid or 0):int(cnt) for uid,cnt in db.execute(
                "SELECT owner_user_id,COUNT(*) FROM color_cards GROUP BY owner_user_id").fetchall()}
        owners=sorted(set(wb)|set(cards))
        return [{'owner_user_id':uid,'workbenches':wb.get(uid,0),'color_cards':cards.get(uid,0)} for uid in owners]

    def transfer_private_ownership(self, source_user_id: int, target_user_id: int) -> dict:
        """Transfer ownership metadata without reading the private payloads."""
        source=int(source_user_id); target=int(target_user_id)
        if source<=0 or target<=0 or source==target:
            raise ValueError('请选择两个不同的有效用户')
        self._create_workbench_schema()
        with self.connect() as db:
            wcur=db.execute('UPDATE workbenches SET owner_user_id=? WHERE owner_user_id=?',(target,source))
            ccur=db.execute('UPDATE color_cards SET owner_user_id=? WHERE owner_user_id=?',(target,source))
            return {'workbenches':int(wcur.rowcount or 0),'color_cards':int(ccur.rowcount or 0)}

    # ---------------- 列设置 ----------------
    def load_column_settings(self, scope: str) -> list[str]:
        with self.connect() as db:
            row = db.execute(
                "SELECT hidden_columns FROM column_settings WHERE scope=?", (scope,)
            ).fetchone()
        if not row:
            return []
        try:
            return json.loads(row[0])
        except Exception:
            return []

    def save_column_settings(self, scope: str, hidden_keys: list[str]) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO column_settings(scope, hidden_columns) VALUES(?,?) "
                "ON CONFLICT(scope) DO UPDATE SET hidden_columns=excluded.hidden_columns",
                (scope, json.dumps(hidden_keys, ensure_ascii=False)),
            )

    # ---------------- 工作台 ----------------
    def _create_workbench_schema(self) -> None:
        with self.connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS workbenches (
                    workbench_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    standard_key TEXT,
                    created_at REAL NOT NULL DEFAULT 0,
                    is_collapsed INTEGER NOT NULL DEFAULT 0,
                    samples_data TEXT NOT NULL DEFAULT '[]',
                    average_standard TEXT,
                    hidden_columns TEXT NOT NULL DEFAULT '[]',
                    settings_json TEXT NOT NULL DEFAULT '{}',
                    owner_user_id INTEGER NOT NULL DEFAULT 0
                )"""
            )
            # 自动补列（迁移旧库）
            cols = {row[1] for row in db.execute("PRAGMA table_info(workbenches)").fetchall()}
            if "samples_data" not in cols:
                db.execute("ALTER TABLE workbenches ADD COLUMN samples_data TEXT NOT NULL DEFAULT '[]'")
            if "average_standard" not in cols:
                db.execute("ALTER TABLE workbenches ADD COLUMN average_standard TEXT")
            if "hidden_columns" not in cols:
                db.execute("ALTER TABLE workbenches ADD COLUMN hidden_columns TEXT NOT NULL DEFAULT '[]'")
            if "settings_json" not in cols:
                db.execute("ALTER TABLE workbenches ADD COLUMN settings_json TEXT NOT NULL DEFAULT '{}'")
            if "owner_user_id" not in cols:
                db.execute("ALTER TABLE workbenches ADD COLUMN owner_user_id INTEGER NOT NULL DEFAULT 0")
            # 兼容旧表
            db.execute(
                """CREATE TABLE IF NOT EXISTS workbench_samples (
                    workbench_id TEXT NOT NULL,
                    sample_key TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    PRIMARY KEY (workbench_id, sample_key)
                )"""
            )

    def list_workbenches(self) -> list[dict]:
        self._create_workbench_schema()
        owner_where, owner_params = self._owner_where()
        where=(' WHERE '+owner_where) if owner_where else ''
        with self.connect() as db:
            rows = db.execute(
                "SELECT workbench_id, name, standard_key, created_at, is_collapsed, "
                "samples_data, average_standard, hidden_columns, settings_json, owner_user_id FROM workbenches"+where+" ORDER BY created_at",
                owner_params,
            ).fetchall()
        result = []
        for wid, name, std_key, created, collapsed, samples_data, avg_std, hidden, settings_json, owner_uid in rows:
            try: samples = json.loads(samples_data) if samples_data else []
            except Exception: samples = []
            try: avg = json.loads(avg_std) if avg_std else None
            except Exception: avg = None
            try: hidden_cols = json.loads(hidden) if hidden else []
            except Exception: hidden_cols = []
            try: settings = json.loads(settings_json) if settings_json else {}
            except Exception: settings = {}
            wb={
                "workbench_id": wid, "name": name, "standard_key": std_key,
                "created_at": created, "is_collapsed": bool(collapsed),
                "samples_data": samples, "average_standard": avg,
                "hidden_columns": hidden_cols, "owner_user_id": int(owner_uid or 0),
            }
            wb.update(settings if isinstance(settings,dict) else {})
            wb.setdefault("illuminants",["D65"]); wb.setdefault("observer",10)
            result.append(wb)
        return result

    def save_workbench(self, wb: dict) -> None:
        self._create_workbench_schema()
        persisted={k:wb.get(k) for k in ("illuminants","observer","shade555","sort_key","sort_desc","average_explicit") if k in wb}
        with self.connect() as db:
            existing=db.execute('SELECT owner_user_id FROM workbenches WHERE workbench_id=?',(wb["workbench_id"],)).fetchone()
            if existing is not None:
                owner=int(existing[0] or 0)
                if owner not in (0,self._current_user_id):
                    raise PermissionError('该比色工作台属于其他用户；系统管理员默认也不能直接读取或修改私人内容')
            else:
                owner=int(wb.get('owner_user_id', self._current_user_id) or self._current_user_id)
            db.execute(
                """INSERT INTO workbenches(workbench_id, name, standard_key, created_at,
                   is_collapsed, samples_data, average_standard, hidden_columns, settings_json, owner_user_id)
                   VALUES(?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(workbench_id) DO UPDATE SET
                     name=excluded.name, standard_key=excluded.standard_key,
                     is_collapsed=excluded.is_collapsed, samples_data=excluded.samples_data,
                     average_standard=excluded.average_standard, hidden_columns=excluded.hidden_columns,
                     settings_json=excluded.settings_json""",
                (wb["workbench_id"], wb["name"], wb.get("standard_key"), wb.get("created_at",0.0),
                 1 if wb.get("is_collapsed") else 0,
                 json.dumps(wb.get("samples_data",[]),ensure_ascii=False),
                 json.dumps(wb.get("average_standard"),ensure_ascii=False) if wb.get("average_standard") else None,
                 json.dumps(wb.get("hidden_columns",[]),ensure_ascii=False),
                 json.dumps(persisted,ensure_ascii=False), owner),
            )
        wb['owner_user_id']=owner
        try: write_workbench_snapshot(wb, owner, self._current_username if owner==self._current_user_id else f'user{owner}')
        except Exception: pass

    def remove_workbench(self, workbench_id: str) -> None:
        self._create_workbench_schema()
        removed_owner = None
        with self.connect() as db:
            row=db.execute('SELECT owner_user_id FROM workbenches WHERE workbench_id=?',(workbench_id,)).fetchone()
            if row is None or int(row[0] or 0) not in (0,self._current_user_id):
                return
            removed_owner=int(row[0] or 0)
            db.execute("DELETE FROM workbenches WHERE workbench_id=?", (workbench_id,))
            db.execute("DELETE FROM workbench_samples WHERE workbench_id=?", (workbench_id,))
        if removed_owner is not None:
            try: remove_workbench_snapshot(workbench_id, removed_owner, self._current_username if removed_owner==self._current_user_id else f'user{removed_owner}')
            except Exception: pass
