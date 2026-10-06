from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import sqlite3
import tempfile
import time
import uuid
import zipfile
from pathlib import Path

from PySide6.QtCore import QStandardPaths

from .storage_v2 import (
    audit_backup_root,
    audit_database_path,
    business_backup_root,
    business_data_root,
    color_database_path,
    formal_library_root,
    full_system_backup_root,
    official_library_root,
    personal_workspace_root,
    security_backup_root,
    security_database_path,
    system_data_root,
    user_workfile_root as _v2_user_workfile_root,
    user_workspace_root as _v2_user_workspace_root,
)


def app_data_root() -> Path:
    """Per-Windows-profile application state root (settings/installation identity only)."""
    root = Path(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation))
    root.mkdir(parents=True, exist_ok=True)
    return root


def managed_data_root() -> Path:
    """Backward-compatible alias for the DG4 human-readable business root."""
    return business_data_root()


def _safe_component(text: str) -> str:
    import re
    text = re.sub(r'[^0-9A-Za-z\-_.\u4e00-\u9fff]+', '_', str(text or '').strip())
    return text.strip('._ ') or 'user'


def user_private_root(user_id: int, username: str) -> Path:
    return _v2_user_workspace_root(user_id, username)


def user_workfile_root(user_id: int, username: str) -> Path:
    return _v2_user_workfile_root(user_id, username)


def library_mirror_root() -> Path:
    return formal_library_root()


def backup_root() -> Path:
    """Legacy API: full-system safety backups live in protected system storage."""
    return full_system_backup_root()


def _sqlite_backup(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(source)
    try:
        dst = sqlite3.connect(destination)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def _new_backup_folder(root: Path, prefix: str = "") -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S") + f"_{int((time.time()%1)*1000):03d}"
    name = f"{prefix}{stamp}" if prefix else stamp
    folder = root / name
    n = 2
    while folder.exists():
        folder = root / f"{name}_{n}"; n += 1
    folder.mkdir(parents=True, exist_ok=False)
    return folder


def _copy_business_payload(destination: Path) -> int:
    """Copy readable business mirrors, excluding backup recursion and exchange temp files."""
    count = 0
    source_root = business_data_root()
    for name in ("01 正式色库", "02 色卡编排", "03 个人工作区"):
        src = source_root / name
        if not src.exists():
            continue
        dst = destination / name
        shutil.copytree(src, dst, dirs_exist_ok=True)
        count += sum(1 for p in src.rglob('*') if p.is_file())
    return count


def _copy_official_resources(destination: Path) -> int:
    src = official_library_root()
    if not src.exists():
        return 0
    shutil.copytree(src, destination / "OfficialLibraries", dirs_exist_ok=True)
    return sum(1 for p in src.rglob('*') if p.is_file())


def create_database_backup(
    auth_db: str | Path,
    library_db: str | Path,
    *, keep: int = 20,
    include_business_files: bool = False,
) -> Path:
    """Create a DG4 full-system safety snapshot.

    Compatibility note: callers still pass the security and colour DB paths.
    The dedicated audit DB is discovered automatically. Automatic daily backup
    keeps the old fast DB-only behaviour; administrator manual full backup may
    opt into readable business mirrors as well.
    """
    root = backup_root()
    folder = _new_backup_folder(root)
    dbdir = folder / "databases"; dbdir.mkdir(parents=True, exist_ok=True)
    auth_db = Path(auth_db); library_db = Path(library_db); audit_db = audit_database_path()
    if auth_db.exists(): _sqlite_backup(auth_db, dbdir / "security.sqlite3")
    if library_db.exists(): _sqlite_backup(library_db, dbdir / "color_data.sqlite3")
    if audit_db.exists(): _sqlite_backup(audit_db, dbdir / "audit.sqlite3")
    business_files = 0
    official_files = 0
    if include_business_files:
        business_files = _copy_business_payload(folder / "business_data")
        official_files = _copy_official_resources(folder / "system_resources")
    manifest = {
        "format": "chromatic-dg4-backup",
        "version": 1,
        "created_at": time.time(),
        "contains": ["security", "color", "audit"] + (["business_mirrors"] if include_business_files else []),
        "business_files": business_files,
        "official_files": official_files,
    }
    (folder / "BACKUP_INFO.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (folder / "BACKUP_INFO.txt").write_text(
        "Chromatic Analysis DG4 full-system backup\n"
        f"created_at={time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        "databases=security + color + audit\n"
        f"business_mirrors={'yes' if include_business_files else 'no'}\n",
        encoding="utf-8",
    )
    backups = sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: p.name, reverse=True)
    for old in backups[max(1, int(keep)):]:
        shutil.rmtree(old, ignore_errors=True)
    return folder


def create_color_data_backup(library_db: str | Path, *, keep: int = 30) -> Path:
    root = business_backup_root() / "色彩数据"
    root.mkdir(parents=True, exist_ok=True)
    folder = _new_backup_folder(root)
    dbdir = folder / "databases"; dbdir.mkdir()
    library_db = Path(library_db)
    if library_db.exists(): _sqlite_backup(library_db, dbdir / "color_data.sqlite3")
    count = _copy_business_payload(folder / "business_data")
    official_count = _copy_official_resources(folder / "system_resources")
    (folder / "BACKUP_INFO.json").write_text(json.dumps({
        "format":"chromatic-color-backup","version":1,"created_at":time.time(),"business_files":count,"official_files":official_count
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    backups = sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p:p.name, reverse=True)
    for old in backups[max(1,int(keep)):]: shutil.rmtree(old, ignore_errors=True)
    return folder


def create_security_backup(auth_db: str | Path, *, keep: int = 30) -> Path:
    root = security_backup_root()
    folder = _new_backup_folder(root)
    auth_db = Path(auth_db)
    if auth_db.exists(): _sqlite_backup(auth_db, folder / "security.sqlite3")
    (folder / "BACKUP_INFO.json").write_text(json.dumps({
        "format":"chromatic-security-backup","version":1,"created_at":time.time()
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    backups = sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p:p.name, reverse=True)
    for old in backups[max(1,int(keep)):]: shutil.rmtree(old, ignore_errors=True)
    return folder


def archive_audit_database(*, keep: int = 50) -> Path:
    root = audit_backup_root()
    folder = _new_backup_folder(root, "Audit_")
    source = audit_database_path()
    if source.exists(): _sqlite_backup(source, folder / "audit.sqlite3")
    backups = sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p:p.name, reverse=True)
    for old in backups[max(1,int(keep)):]: shutil.rmtree(old, ignore_errors=True)
    return folder


def maybe_daily_backup(settings, auth_db: str | Path, library_db: str | Path) -> Path | None:
    """At most one lightweight database-only safety snapshot per 24 hours."""
    now = time.time()
    try:
        last = float(settings.value("data_governance/last_backup", 0) or 0)
    except Exception:
        last = 0.0
    if now - last < 24 * 3600:
        return None
    folder = create_database_backup(auth_db, library_db, include_business_files=False)
    settings.setValue("data_governance/last_backup", now)
    return folder


def _find_backup_db(folder: Path, new_name: str, *legacy_names: str) -> Path | None:
    candidates = [folder / "databases" / new_name, folder / new_name]
    candidates.extend(folder / name for name in legacy_names if name)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _replace_db(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".restore.tmp")
    shutil.copy2(source, temporary)
    os.replace(temporary, destination)


def _replace_tree(source: Path, destination: Path) -> None:
    """Replace a directory domain without leaving stale files from newer state."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.parent / (destination.name + ".restore.tmp")
    old = destination.parent / (destination.name + ".restore.old")
    shutil.rmtree(temp, ignore_errors=True); shutil.rmtree(old, ignore_errors=True)
    shutil.copytree(source, temp)
    if destination.exists():
        destination.rename(old)
    temp.rename(destination)
    shutil.rmtree(old, ignore_errors=True)


def restore_database_backup(folder: str | Path, auth_db: str | Path, library_db: str | Path) -> None:
    """Restore a full backup; old DG3 two-database folders remain supported."""
    folder = Path(folder); auth_db = Path(auth_db); library_db = Path(library_db)
    src_auth = _find_backup_db(folder, "security.sqlite3", auth_db.name, "chromatic_auth.sqlite3", "auth.sqlite3")
    src_lib = _find_backup_db(folder, "color_data.sqlite3", library_db.name, "chromatic_library.sqlite3", "library.sqlite3")
    if not src_auth or not src_lib:
        raise ValueError("该备份不完整，缺少用户权限数据库或色彩数据库")
    create_database_backup(auth_db, library_db, keep=20, include_business_files=True)
    _replace_db(src_auth, auth_db)
    _replace_db(src_lib, library_db)
    src_audit = _find_backup_db(folder, "audit.sqlite3")
    if src_audit:
        _replace_db(src_audit, audit_database_path())
    else:
        # Legacy DG3 backups keep audit_log inside chromatic_auth.sqlite3. Start
        # with a fresh audit DB so AuthStore can migrate that embedded history
        # on the next launch instead of mixing it with the pre-restore audit.
        audit_database_path().unlink(missing_ok=True)
    business = folder / "business_data"
    if business.exists():
        for name in ("01 正式色库", "02 色卡编排", "03 个人工作区"):
            src = business / name
            if src.exists():
                _replace_tree(src, business_data_root() / name)
    official = folder / "system_resources" / "OfficialLibraries"
    if official.exists():
        _replace_tree(official, official_library_root())


def restore_color_data_backup(folder: str | Path, library_db: str | Path, auth_db: str | Path | None = None) -> None:
    folder = Path(folder); library_db = Path(library_db)
    src = _find_backup_db(folder, "color_data.sqlite3", library_db.name, "chromatic_library.sqlite3", "library.sqlite3")
    if not src:
        raise ValueError("该备份不包含色彩数据库")
    create_database_backup(auth_db or security_database_path(), library_db, include_business_files=True)
    _replace_db(src, library_db)
    business = folder / "business_data"
    if business.exists():
        for name in ("01 正式色库", "02 色卡编排", "03 个人工作区"):
            source = business / name
            if source.exists(): _replace_tree(source, business_data_root() / name)
    official = folder / "system_resources" / "OfficialLibraries"
    if official.exists():
        _replace_tree(official, official_library_root())


def restore_security_backup(folder: str | Path, auth_db: str | Path, library_db: str | Path | None = None) -> None:
    folder = Path(folder); auth_db = Path(auth_db)
    src = _find_backup_db(folder, "security.sqlite3", auth_db.name, "chromatic_auth.sqlite3", "auth.sqlite3")
    if not src:
        raise ValueError("该备份不包含用户与权限数据库")
    create_database_backup(auth_db, library_db or color_database_path(), include_business_files=False)
    _replace_db(src, auth_db)


# ---------------- DG-3.1 / DG4 installation identity / portable package ----------------
PACKAGE_FORMAT = "chromatic-data-package"
PACKAGE_VERSION = 2


def installation_metadata_path() -> Path:
    return app_data_root() / "installation.json"


def installation_info() -> dict:
    path = installation_metadata_path()
    data = {}
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict): data = raw
        except Exception: data = {}
    changed = False
    if not str(data.get("installation_id") or "").strip():
        data["installation_id"] = str(uuid.uuid4()); changed = True
    if not str(data.get("label") or "").strip():
        data["label"] = socket.gethostname().strip() or "PC"; changed = True
    if not data.get("created_at"):
        data["created_at"] = time.time(); changed = True
    if changed or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return dict(data)


def set_installation_label(label: str) -> dict:
    data = installation_info(); label = str(label or "").strip()
    if not label: raise ValueError("安装实例名称不能为空")
    data["label"] = label
    installation_metadata_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return dict(data)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""): h.update(chunk)
    return h.hexdigest()


def _copy_tree_if_exists(source: Path, destination: Path) -> None:
    if not source.exists(): return
    if destination.exists(): shutil.rmtree(destination)
    shutil.copytree(source, destination)


def export_portable_data_package(auth_db: str | Path, library_db: str | Path, destination: str | Path,
                                 *, organization_name: str = "") -> Path:
    """Export DG4 package: security, colour, audit and readable business data."""
    auth_db = Path(auth_db); library_db = Path(library_db); destination = Path(destination)
    if destination.suffix.lower() != ".cadata": destination = destination.with_suffix(".cadata")
    destination.parent.mkdir(parents=True, exist_ok=True)
    info = installation_info()
    with tempfile.TemporaryDirectory(prefix="chromatic_pkg_") as td:
        stage = Path(td) / "package"; stage.mkdir(parents=True, exist_ok=True)
        dbdir = stage / "databases"; dbdir.mkdir()
        if auth_db.exists(): _sqlite_backup(auth_db, dbdir / "security.sqlite3")
        if library_db.exists(): _sqlite_backup(library_db, dbdir / "color_data.sqlite3")
        audit_db = audit_database_path()
        if audit_db.exists(): _sqlite_backup(audit_db, dbdir / "audit.sqlite3")
        _copy_business_payload(stage / "business_data")
        _copy_official_resources(stage / "system_resources")
        files = []
        for file in stage.rglob("*"):
            if file.is_file():
                files.append({"path":file.relative_to(stage).as_posix(),"sha256":_sha256(file),"size":file.stat().st_size})
        manifest = {
            "format": PACKAGE_FORMAT, "version": PACKAGE_VERSION, "created_at": time.time(),
            "organization_name": str(organization_name or ""),
            "source_installation_id": info.get("installation_id", ""),
            "source_installation_label": info.get("label", ""),
            "sensitive": True, "encrypted": False, "data_domains":["security","color","audit","business"],
            "files": files,
        }
        (stage / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp = destination.with_suffix(destination.suffix + ".tmp"); tmp.unlink(missing_ok=True)
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for file in stage.rglob("*"):
                if file.is_file(): zf.write(file, file.relative_to(stage).as_posix())
        os.replace(tmp, destination)
    return destination


def inspect_portable_data_package(package: str | Path) -> dict:
    package = Path(package)
    with zipfile.ZipFile(package, "r") as zf:
        try: manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        except Exception as exc: raise ValueError("不是有效的 Chromatic Analysis 数据包") from exc
        version = int(manifest.get("version", 0) or 0)
        if manifest.get("format") != PACKAGE_FORMAT or version not in (1, PACKAGE_VERSION):
            raise ValueError("数据包格式或版本不受支持")
    return manifest


def _safe_extract(zf: zipfile.ZipFile, target: Path) -> None:
    base = target.resolve()
    for member in zf.infolist():
        candidate = (target / member.filename).resolve()
        if candidate != base and base not in candidate.parents: raise ValueError("数据包包含非法路径")
    zf.extractall(target)


def rebase_library_mirror_paths(library_db: str | Path) -> int:
    library_db = Path(library_db)
    if not library_db.exists(): return 0
    changed = 0; root = library_mirror_root()
    with sqlite3.connect(library_db) as db:
        try: rows = db.execute("SELECT path,customer,mirror_path FROM saved_qtx WHERE mirror_path<>''").fetchall()
        except sqlite3.DatabaseError: return 0
        for source, customer, old in rows:
            old_path = Path(str(old or ""))
            target = None
            # Preferred deterministic target: customer hierarchy + old filename.
            parts=[p.strip() for p in str(customer or "未分类").replace('\\','/').split('/') if p.strip()]
            if parts and parts[0] == '官方色库':
                search_root=official_library_root(); folder=search_root; parts=parts[1:]
            else:
                search_root=root; folder=root
            for part in parts:
                folder = folder / _safe_component(part)
            candidate = folder / old_path.name if old_path.name else None
            if candidate is not None and candidate.exists(): target = candidate
            if target is None and old_path.name:
                matches = list(search_root.rglob(old_path.name))
                target = matches[0] if len(matches) == 1 else None
            if target is not None and str(target) != str(old):
                db.execute("UPDATE saved_qtx SET mirror_path=? WHERE path=?", (str(target), str(source)))
                changed += 1
    return changed


def import_portable_data_package(package: str | Path, auth_db: str | Path, library_db: str | Path) -> dict:
    package = Path(package); auth_db = Path(auth_db); library_db = Path(library_db)
    manifest = inspect_portable_data_package(package)
    create_database_backup(auth_db, library_db, keep=20, include_business_files=True)
    pre_name = backup_root() / ("pre_import_" + time.strftime("%Y%m%d_%H%M%S") + ".cadata")
    export_portable_data_package(auth_db, library_db, pre_name, organization_name="pre-import safety snapshot")
    with tempfile.TemporaryDirectory(prefix="chromatic_import_") as td:
        stage = Path(td)
        with zipfile.ZipFile(package, "r") as zf: _safe_extract(zf, stage)
        for item in manifest.get("files", []):
            rel = str(item.get("path") or ""); path = stage / rel
            if not path.exists() or _sha256(path) != str(item.get("sha256") or ""):
                raise ValueError(f"数据包校验失败：{rel}")
        version = int(manifest.get("version", 1) or 1)
        if version == 1:
            src_auth = stage / "databases" / "auth.sqlite3"
            src_lib = stage / "databases" / "library.sqlite3"
        else:
            src_auth = stage / "databases" / "security.sqlite3"
            src_lib = stage / "databases" / "color_data.sqlite3"
        if not src_auth.exists() or not src_lib.exists(): raise ValueError("数据包缺少必要数据库")
        _replace_db(src_auth, auth_db); _replace_db(src_lib, library_db)
        if version >= 2:
            src_audit = stage / "databases" / "audit.sqlite3"
            if src_audit.exists(): _replace_db(src_audit, audit_database_path())
            business = stage / "business_data"
            if business.exists():
                for name in ("01 正式色库", "02 色卡编排", "03 个人工作区"):
                    src = business / name
                    if src.exists(): _replace_tree(src, business_data_root()/name)
            official = stage / "system_resources" / "OfficialLibraries"
            if official.exists(): _replace_tree(official, official_library_root())
        else:
            imported_managed = stage / "managed_data"
            mirrors = imported_managed / "library_mirrors" / "Customers"
            if mirrors.exists(): shutil.copytree(mirrors, formal_library_root(), dirs_exist_ok=True)
            users = imported_managed / "users"
            if users.exists():
                for user_dir in users.iterdir():
                    if user_dir.is_dir() and (user_dir/"workfiles").exists():
                        shutil.copytree(user_dir/"workfiles", personal_workspace_root()/user_dir.name/"个人文件", dirs_exist_ok=True)
            # v1 audit history is embedded in auth.sqlite3; AuthStore migrates it
            # into the dedicated audit DB on the next application start.
            audit_database_path().unlink(missing_ok=True)
    manifest = dict(manifest)
    manifest["rebased_mirror_paths"] = rebase_library_mirror_paths(library_db)
    manifest["target_installation_id"] = installation_info().get("installation_id", "")
    return manifest


def managed_workfile_count(user_id: int, username: str) -> int:
    root = user_workfile_root(user_id, username)
    try: return sum(1 for p in root.rglob('*') if p.is_file())
    except Exception: return 0


def transfer_managed_workfiles(source_user_id: int, source_username: str,
                               target_user_id: int, target_username: str) -> int:
    src = user_workfile_root(source_user_id, source_username)
    dst = user_workfile_root(target_user_id, target_username)
    moved = 0
    if not src.exists(): return 0
    for file in list(src.rglob('*')):
        if not file.is_file(): continue
        rel = file.relative_to(src); target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            stem, suf = target.stem, target.suffix; n = 2
            while (target.parent / f"{stem}_{n}{suf}").exists(): n += 1
            target = target.parent / f"{stem}_{n}{suf}"
        shutil.move(str(file), str(target)); moved += 1
    for folder in sorted([p for p in src.rglob('*') if p.is_dir()], key=lambda p:len(p.parts), reverse=True):
        try: folder.rmdir()
        except Exception: pass
    return moved
