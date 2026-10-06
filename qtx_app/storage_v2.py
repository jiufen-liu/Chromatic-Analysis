from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sqlite3
import time
from pathlib import Path

from PySide6.QtCore import QStandardPaths
from .atomic_json import atomic_write_json

logger = logging.getLogger(__name__)


DG4_LAYOUT_VERSION = 3
CONFIG_FILE = "storage_v2.json"


def _profile_app_data_root() -> Path:
    root = Path(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation))
    root.mkdir(parents=True, exist_ok=True)
    return root


def storage_config_path() -> Path:
    return _profile_app_data_root() / CONFIG_FILE


def _read_config() -> dict:
    path = storage_config_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _write_config(data: dict) -> None:
    path = storage_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, data)


def _can_use_directory(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".chromatic_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def _choose_system_root() -> Path:
    cfg = _read_config()
    saved = str(cfg.get("system_root") or "").strip()
    if saved:
        root = Path(saved)
        if _can_use_directory(root):
            return root

    candidates: list[Path] = []
    if os.name == "nt":
        program_data = str(os.environ.get("PROGRAMDATA") or "").strip()
        if program_data:
            candidates.append(Path(program_data) / "Chromatic Analysis")
    generic = str(QStandardPaths.writableLocation(QStandardPaths.GenericDataLocation) or "").strip()
    if generic:
        candidates.append(Path(generic) / "Chromatic Analysis")
    candidates.append(_profile_app_data_root() / "system_data")

    chosen = candidates[-1]
    for candidate in candidates:
        if _can_use_directory(candidate):
            chosen = candidate
            break
    cfg["system_root"] = str(chosen)
    cfg.setdefault("layout_version", DG4_LAYOUT_VERSION)
    _write_config(cfg)
    return chosen


def system_data_root() -> Path:
    root = _choose_system_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def default_business_data_root() -> Path:
    docs = str(QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation) or "").strip()
    base = Path(docs) if docs else _profile_app_data_root()
    return base / "Chromatic Analysis Data"


def business_data_root() -> Path:
    cfg = _read_config()
    raw = str(cfg.get("business_root") or "").strip()
    root = Path(raw) if raw else default_business_data_root()
    root.mkdir(parents=True, exist_ok=True)
    if not raw:
        cfg["business_root"] = str(root)
        cfg.setdefault("layout_version", DG4_LAYOUT_VERSION)
        _write_config(cfg)
    ensure_business_layout(root)
    return root


def ensure_business_layout(root: Path | None = None) -> Path:
    root = Path(root) if root is not None else business_data_root()
    folders = [
        root / "01 正式色库",
        root / "02 色卡编排",
        root / "02 色卡编排" / "用户方案",
        root / "02 色卡编排" / "共享方案",
        root / "02 色卡编排" / "已发布方案",
        root / "02 色卡编排" / "历史未归属",
        root / "03 个人工作区",
        root / "04 数据交换" / "导入",
        root / "04 数据交换" / "导出" / "QTX",
        root / "04 数据交换" / "导出" / "CPX",
        root / "04 数据交换" / "导出" / "Excel",
        root / "04 数据交换" / "导出" / "PDF",
        root / "04 数据交换" / "导出" / "图片",
        root / "90 业务备份",
        root / "99 旧版迁移源",
    ]
    for folder in folders:
        folder.mkdir(parents=True, exist_ok=True)
    guide = root / "README_数据目录.txt"
    guide_text = (
        "Chromatic Analysis 业务数据目录\n"
        "\n"
        "01 正式色库：客户/项目正式色库的可读 QTX 镜像。\n"
        "02 色卡编排：色卡方案可读镜像。用户方案=私人草稿；共享方案=所有者不变但组织可见；已发布方案=正式业务输出；历史未归属=升级前无法可靠判断所有者的数据。\n"
        "03 个人工作区：个人比色工作台与 .chromatic 工作文件。\n"
        "04 数据交换：导入/导出的默认分类目录。\n"
        "90 业务备份：仅色彩业务数据的人工备份。\n"
        "99 旧版迁移源：版本升级时保留的旧目录快照，仅用于回滚/核对，不是当前正式数据源。\n"
        "\n"
        "注意：这里不是账号/密码数据库。用户、权限、审计与系统数据库由程序单独保护管理。\n"
        "请优先通过 Chromatic Analysis 的‘数据管理中心’迁移/备份数据，不要手工移动正在使用的数据库。\n"
    )
    try:
        if not guide.exists() or guide.read_text(encoding="utf-8") != guide_text:
            guide.write_text(guide_text, encoding="utf-8")
    except Exception:
        pass
    return root


def ensure_system_layout() -> Path:
    root = system_data_root()
    for folder in (
        root / "Security",
        root / "Database",
        root / "Audit",
        root / "OfficialLibraries",
        root / "System",
        root / "Backup" / "Security",
        root / "Backup" / "System",
        root / "Backup" / "Audit",
        root / "Runtime",
    ):
        folder.mkdir(parents=True, exist_ok=True)
    return root


def formal_library_root() -> Path:
    root = business_data_root() / "01 正式色库"
    root.mkdir(parents=True, exist_ok=True)
    return root


def color_card_root() -> Path:
    root = business_data_root() / "02 色卡编排"
    root.mkdir(parents=True, exist_ok=True)
    return root


def personal_workspace_root() -> Path:
    root = business_data_root() / "03 个人工作区"
    root.mkdir(parents=True, exist_ok=True)
    return root


def exchange_root() -> Path:
    return business_data_root() / "04 数据交换"


def exchange_import_root() -> Path:
    root = exchange_root() / "导入"; root.mkdir(parents=True, exist_ok=True); return root


def exchange_export_root(kind: str = "") -> Path:
    root = exchange_root() / "导出"
    if kind:
        root = root / safe_component(kind)
    root.mkdir(parents=True, exist_ok=True)
    return root


def business_backup_root() -> Path:
    root = business_data_root() / "90 业务备份"
    root.mkdir(parents=True, exist_ok=True)
    return root


def legacy_archive_root() -> Path:
    root = business_data_root() / "99 旧版迁移源"
    root.mkdir(parents=True, exist_ok=True)
    return root


def security_root() -> Path:
    root = ensure_system_layout() / "Security"; root.mkdir(parents=True, exist_ok=True); return root


def database_root() -> Path:
    root = ensure_system_layout() / "Database"; root.mkdir(parents=True, exist_ok=True); return root


def audit_root() -> Path:
    root = ensure_system_layout() / "Audit"; root.mkdir(parents=True, exist_ok=True); return root


def official_library_root() -> Path:
    root = ensure_system_layout() / "OfficialLibraries"; root.mkdir(parents=True, exist_ok=True); return root


def runtime_root() -> Path:
    root = ensure_system_layout() / "Runtime"; root.mkdir(parents=True, exist_ok=True); return root


def full_system_backup_root() -> Path:
    root = ensure_system_layout() / "Backup" / "System"; root.mkdir(parents=True, exist_ok=True); return root


def security_backup_root() -> Path:
    root = ensure_system_layout() / "Backup" / "Security"; root.mkdir(parents=True, exist_ok=True); return root


def audit_backup_root() -> Path:
    root = ensure_system_layout() / "Backup" / "Audit"; root.mkdir(parents=True, exist_ok=True); return root


def security_database_path() -> Path:
    return security_root() / "security.sqlite3"


def color_database_path() -> Path:
    return database_root() / "color_data.sqlite3"


def audit_database_path() -> Path:
    return audit_root() / "audit.sqlite3"


def legacy_auth_database_path() -> Path:
    return _profile_app_data_root() / "chromatic_auth.sqlite3"


def legacy_color_database_path() -> Path:
    return _profile_app_data_root() / "chromatic_library.sqlite3"


def legacy_managed_data_root() -> Path:
    return _profile_app_data_root() / "managed_data"


def safe_component(text: str, default: str = "未分类") -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', '_', str(text or '').strip())
    value = re.sub(r'\s+', ' ', value).rstrip('. ')
    return value or default


def user_folder_name(user_id: int, username: str = "") -> str:
    label = safe_component(username, "user")
    return f"{int(user_id):04d}_{label}"


def user_workspace_root(user_id: int, username: str = "") -> Path:
    root = personal_workspace_root() / user_folder_name(user_id, username)
    root.mkdir(parents=True, exist_ok=True)
    return root


def user_workfile_root(user_id: int, username: str = "") -> Path:
    root = user_workspace_root(user_id, username) / "个人文件"
    root.mkdir(parents=True, exist_ok=True)
    return root


def user_workbench_snapshot_root(user_id: int, username: str = "") -> Path:
    root = user_workspace_root(user_id, username) / "比色工作台"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _write_owner_metadata(root: Path, user_id: int, username: str, scope: str) -> None:
    try:
        meta = root / "_owner.json"
        payload = {
            "scope": str(scope),
            "owner_user_id": int(user_id or 0),
            "username": str(username or ""),
            "generated_by": "Chromatic Analysis DG4",
            "layout_version": DG4_LAYOUT_VERSION,
        }
        tmp = meta.with_suffix(meta.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, meta)
    except Exception:
        pass


def user_color_card_snapshot_root(user_id: int, username: str = "") -> Path:
    """Private colour-card mirror root for one user.

    DG4.1 separates *ownership* from *visibility*.  This compatibility helper
    therefore represents only the owner's private area; shared/published cards
    are routed by :func:`color_card_snapshot_root`.
    """
    uid = int(user_id or 0)
    if uid <= 0:
        root = color_card_root() / "历史未归属"
        label = str(username or "历史未归属")
        scope = "legacy_unassigned"
    else:
        root = color_card_root() / "用户方案" / safe_component(username, f"user{uid}")
        label = str(username or f"user{uid}")
        scope = "private"
    root.mkdir(parents=True, exist_ok=True)
    _write_owner_metadata(root, uid, label, scope)
    return root


def color_card_snapshot_root(user_id: int, username: str = "", *, visibility: str = "private", lifecycle_status: str = "draft") -> Path:
    """Return the human-readable mirror root without changing logical ownership.

    - private: 用户方案/<username>
    - organization: 共享方案 (owner remains in metadata/SQLite)
    - published: 已发布方案 (organization-visible formal business output)
    - owner 0 / legacy_unassigned: 历史未归属
    """
    uid = int(user_id or 0)
    visibility = str(visibility or "private")
    lifecycle_status = str(lifecycle_status or "draft")
    if uid <= 0 or visibility == "legacy_unassigned":
        root = color_card_root() / "历史未归属"
        scope = "legacy_unassigned"
        label = str(username or "历史未归属")
    elif lifecycle_status == "published":
        root = color_card_root() / "已发布方案"
        scope = "published"
        label = str(username or f"user{uid}")
    elif visibility == "organization":
        root = color_card_root() / "共享方案"
        scope = "organization"
        label = str(username or f"user{uid}")
    else:
        return user_color_card_snapshot_root(uid, username)
    root.mkdir(parents=True, exist_ok=True)
    _write_owner_metadata(root, uid, label, scope)
    return root


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


def _migrate_database_once(source: Path, destination: Path) -> bool:
    if destination.exists() or not source.exists():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_suffix(destination.suffix + ".migrate.tmp")
    tmp.unlink(missing_ok=True)
    _sqlite_backup(source, tmp)
    os.replace(tmp, destination)
    return True


def migrate_legacy_security_database() -> bool:
    ensure_system_layout()
    return _migrate_database_once(legacy_auth_database_path(), security_database_path())


def migrate_legacy_color_database() -> bool:
    ensure_system_layout()
    return _migrate_database_once(legacy_color_database_path(), color_database_path())


def _copy_tree_merge(source: Path, destination: Path) -> int:
    if not source.exists():
        return 0
    count = 0
    for item in source.rglob("*"):
        rel = item.relative_to(source)
        target = destination / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif item.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists() or item.stat().st_mtime > target.stat().st_mtime or item.stat().st_size != target.stat().st_size:
                shutil.copy2(item, target)
            count += 1
    return count


def migrate_legacy_business_files() -> dict:
    """Copy legacy DG-3 business files into the readable DG-4 tree.

    The old files are deliberately preserved as a rollback source. Database paths
    are rebased separately by LibraryStore after it has opened the migrated DB.
    """
    ensure_business_layout()
    marker = ensure_system_layout() / "System" / "dg4_business_migration.json"
    if marker.exists():
        try:
            return json.loads(marker.read_text(encoding="utf-8"))
        except Exception:
            return {"already_migrated": True}

    report = {"created_at": time.time(), "managed_mirrors": 0, "documents_customers": 0, "personal_files": 0}
    legacy_managed = legacy_managed_data_root()
    legacy_mirrors = legacy_managed / "library_mirrors" / "Customers"
    report["managed_mirrors"] = _copy_tree_merge(legacy_mirrors, formal_library_root())
    # Official libraries are system-managed/read-only resources in DG4. Copy
    # their legacy mirrors into protected system storage while keeping the old
    # source intact for rollback.
    report["official_library_files"] = _copy_tree_merge(
        legacy_mirrors / "官方色库", official_library_root()
    )
    old_docs = default_business_data_root() / "Customers"
    # If the configured business root is still the default, copy from the old
    # pre-DG folder into the new numbered folder. Source remains intact.
    report["documents_customers"] = _copy_tree_merge(old_docs, formal_library_root())
    report["official_library_files"] += _copy_tree_merge(old_docs / "官方色库", official_library_root())
    # Do not expose official-library mirrors in the user-facing formal-library
    # folder. Only the newly created DG4 copy is removed; legacy source folders
    # are preserved untouched for rollback.
    duplicate_official = formal_library_root() / "官方色库"
    if duplicate_official.exists():
        shutil.rmtree(duplicate_official, ignore_errors=True)

    users = legacy_managed / "users"
    if users.exists():
        for user_dir in users.iterdir():
            if not user_dir.is_dir():
                continue
            match = re.match(r"^(\d+)_(.*)$", user_dir.name)
            if match:
                uid = int(match.group(1)); username = match.group(2) or f"user{uid}"
                dst = user_workfile_root(uid, username)
            else:
                dst = personal_workspace_root() / safe_component(user_dir.name, "legacy_user") / "个人文件"
            report["personal_files"] += _copy_tree_merge(user_dir / "workfiles", dst)

    try:
        snapshots = rebuild_readable_snapshots(color_database_path(), security_database_path())
        report["color_card_snapshots"] = int(snapshots.get("color_cards", 0))
        report["workbench_snapshots"] = int(snapshots.get("workbenches", 0))
    except Exception:
        report["color_card_snapshots"] = 0
        report["workbench_snapshots"] = 0
    marker.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _remove_snapshot_by_id(root: Path, short_id: str, suffix: str, *, keep_path: Path | None = None) -> None:
    if not root.exists() or not short_id:
        return
    for path in root.rglob(f"*__{short_id}.{suffix}"):
        if keep_path is not None and path == keep_path:
            continue
        try:
            path.unlink()
        except Exception:
            pass


def write_color_card_snapshot(card: dict, user_id: int, username: str = "") -> Path | None:
    try:
        card_id = str(card.get("card_id") or "")
        short_id = card_id.replace("-", "")[:10] or "card"
        visibility = str(card.get("visibility") or ("legacy_unassigned" if int(user_id or 0) <= 0 else "private"))
        lifecycle_status = str(card.get("lifecycle_status") or "draft")
        # Commit the replacement before retiring renamed/moved mirrors.
        root = color_card_snapshot_root(user_id, username, visibility=visibility, lifecycle_status=lifecycle_status)
        customer = str(card.get("customer") or "未分类")
        folder = root
        for part in customer.replace("\\", "/").split("/"):
            if part.strip():
                folder = folder / safe_component(part)
        folder.mkdir(parents=True, exist_ok=True)
        name = safe_component(card.get("name") or "未命名色卡")
        path = folder / f"{name}__{short_id}.chromaticcard.json"
        payload = dict(card)
        payload["format"] = "chromatic-color-card"
        payload["format_version"] = 2
        payload["owner_user_id"] = int(user_id or 0)
        payload["visibility"] = visibility
        payload["lifecycle_status"] = lifecycle_status
        atomic_write_json(path, payload)
        _remove_snapshot_by_id(color_card_root(), short_id, "chromaticcard.json", keep_path=path)
        return path
    except Exception:
        logger.exception("Cannot update colour-card readable mirror")
        return None


def remove_color_card_snapshot(card_id: str, user_id: int = 0, username: str = "") -> None:
    short_id = str(card_id or "").replace("-", "")[:10]
    _remove_snapshot_by_id(color_card_root(), short_id, "chromaticcard.json")


def write_workbench_snapshot(workbench: dict, user_id: int, username: str = "") -> Path | None:
    try:
        wb_id = str(workbench.get("workbench_id") or "")
        short_id = wb_id.replace("-", "")[:10] or "workbench"
        root = user_workbench_snapshot_root(user_id, username)
        name = safe_component(workbench.get("name") or "比色工作台")
        path = root / f"{name}__{short_id}.chromaticworkbench.json"
        payload = dict(workbench)
        payload["format"] = "chromatic-workbench"
        payload["format_version"] = 1
        payload["owner_user_id"] = int(user_id or 0)
        atomic_write_json(path, payload)
        _remove_snapshot_by_id(root, short_id, "chromaticworkbench.json", keep_path=path)
        return path
    except Exception:
        logger.exception("Cannot update workbench readable mirror")
        return None


def remove_workbench_snapshot(workbench_id: str, user_id: int, username: str = "") -> None:
    short_id = str(workbench_id or "").replace("-", "")[:10]
    _remove_snapshot_by_id(user_workbench_snapshot_root(user_id, username), short_id, "chromaticworkbench.json")




def cleanup_readable_snapshots_for_user(user_id: int, username: str = "") -> None:
    """Remove only generated card/workbench mirrors for one logical user.

    Personal .chromatic workfiles are intentionally untouched. This helper is
    used after an administrator transfers ownership, then mirrors are rebuilt
    from authoritative SQLite metadata.
    """
    card_dir = user_color_card_snapshot_root(user_id, username)
    wb_dir = user_workbench_snapshot_root(user_id, username)
    try:
        if card_dir.exists(): shutil.rmtree(card_dir)
    except Exception:
        pass
    try:
        if wb_dir.exists(): shutil.rmtree(wb_dir)
    except Exception:
        pass

def rebuild_readable_snapshots(color_db: Path | None = None, security_db: Path | None = None) -> dict:
    """Rebuild human-readable card/workbench mirrors from SQLite without hydrating spectra."""
    color_db = Path(color_db or color_database_path())
    security_db = Path(security_db or security_database_path())
    users: dict[int, str] = {0: "共享"}
    if security_db.exists():
        try:
            with sqlite3.connect(security_db) as db:
                for uid, username in db.execute("SELECT id,username FROM users").fetchall():
                    users[int(uid)] = str(username or f"user{uid}")
        except Exception:
            pass
    result = {"color_cards": 0, "workbenches": 0}
    if not color_db.exists():
        return result
    with sqlite3.connect(color_db) as db:
        try:
            card_cols = {r[1] for r in db.execute("PRAGMA table_info(color_cards)").fetchall()}
            extra = []
            for name, default in (("visibility", "'private'"), ("lifecycle_status", "'draft'"),
                                  ("created_by_user_id", "0"), ("updated_by_user_id", "0"),
                                  ("published_at", "0"), ("published_by_user_id", "0")):
                extra.append(name if name in card_cols else f"{default} AS {name}")
            rows = db.execute(
                "SELECT card_id,name,customer,columns_count,layout_json,settings_json,created_at,updated_at,owner_user_id," + ",".join(extra) + " FROM color_cards"
            ).fetchall()
            for row in rows:
                uid = int(row[8] or 0)
                try: layout = json.loads(row[4] or "[]")
                except Exception: layout = []
                try: settings = json.loads(row[5] or "{}")
                except Exception: settings = {}
                card = {"card_id":row[0],"name":row[1],"customer":row[2],"columns_count":row[3],
                        "layout":layout,"settings":settings,"created_at":row[6],"updated_at":row[7],"owner_user_id":uid,
                        "visibility":row[9],"lifecycle_status":row[10],"created_by_user_id":int(row[11] or 0),
                        "updated_by_user_id":int(row[12] or 0),"published_at":float(row[13] or 0),
                        "published_by_user_id":int(row[14] or 0)}
                if write_color_card_snapshot(card, uid, users.get(uid, f"user{uid}")):
                    result["color_cards"] += 1
        except sqlite3.DatabaseError:
            pass
        try:
            rows = db.execute(
                "SELECT workbench_id,name,standard_key,created_at,is_collapsed,samples_data,average_standard,hidden_columns,settings_json,owner_user_id FROM workbenches"
            ).fetchall()
            for row in rows:
                uid = int(row[9] or 0)
                def load(value, default):
                    try: return json.loads(value) if value else default
                    except Exception: return default
                wb = {"workbench_id":row[0],"name":row[1],"standard_key":row[2],"created_at":row[3],
                      "is_collapsed":bool(row[4]),"samples_data":load(row[5],[]),"average_standard":load(row[6],None),
                      "hidden_columns":load(row[7],[]),"owner_user_id":uid}
                settings = load(row[8],{})
                if isinstance(settings, dict): wb.update(settings)
                if write_workbench_snapshot(wb, uid, users.get(uid, f"user{uid}")):
                    result["workbenches"] += 1
        except sqlite3.DatabaseError:
            pass
    return result


def _path_is_under(path_value: str | Path, root: Path) -> bool:
    try:
        p = Path(path_value).resolve()
        r = root.resolve()
        return p == r or r in p.parents
    except Exception:
        return False


def legacy_customers_status(color_db: Path | None = None) -> dict:
    """Describe the pre-DG4 ``Documents/Chromatic Analysis Data/Customers`` tree.

    ``saved_qtx.path`` values can still contain the historical source path as a
    stable logical identifier.  That does *not* make the old folder the active
    data source: samples are read from SQLite and writable QTX mirrors live in
    ``01 正式色库``.  ``mirror_path`` references, by contrast, must be fully rebased.
    """
    old_root = default_business_data_root() / "Customers"
    report = {
        "path": str(old_root),
        "exists": old_root.exists(),
        "files": 0,
        "logical_source_refs": 0,
        "active_mirror_refs": 0,
        "authoritative": False,
        "archive_copy": "",
    }
    if old_root.exists():
        try:
            report["files"] = sum(1 for p in old_root.rglob("*") if p.is_file())
        except Exception:
            pass
    db_path = Path(color_db or color_database_path())
    if db_path.exists():
        try:
            with sqlite3.connect(db_path) as db:
                rows = db.execute("SELECT path,mirror_path FROM saved_qtx").fetchall()
            for source, mirror in rows:
                if source and _path_is_under(str(source), old_root):
                    report["logical_source_refs"] += 1
                if mirror and _path_is_under(str(mirror), old_root):
                    report["active_mirror_refs"] += 1
        except Exception:
            pass
    target = legacy_archive_root() / "Customers_旧版迁移源_首次归档"
    if target.exists():
        report["archive_copy"] = str(target)
    return report


def _mark_windows_hidden(path: Path) -> bool:
    if os.name != "nt" or not path.exists():
        return False
    try:
        import ctypes
        FILE_ATTRIBUTE_HIDDEN = 0x2
        FILE_ATTRIBUTE_ARCHIVE = 0x20
        current = ctypes.windll.kernel32.GetFileAttributesW(str(path))
        if current == -1:
            return False
        return bool(ctypes.windll.kernel32.SetFileAttributesW(str(path), current | FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_ARCHIVE))
    except Exception:
        return False


def archive_legacy_customers_snapshot(color_db: Path | None = None) -> dict:
    """Create a rollback copy of the old Customers folder without breaking old IDs.

    The historical folder is deliberately retained in place because some old
    database keys/favourites may still contain its path as a *logical ID*.  The
    application no longer uses it as the writable/authoritative mirror.  On
    Windows we hide the compatibility folder after making the rollback copy so
    ordinary Explorer users see only the DG4 numbered business folders.
    """
    report = legacy_customers_status(color_db)
    old_root = Path(report["path"])
    if not old_root.exists():
        report["action"] = "not_present"
        return report
    target = legacy_archive_root() / "Customers_旧版迁移源_首次归档"
    if not target.exists():
        _copy_tree_merge(old_root, target)
    report["archive_copy"] = str(target)
    note = old_root / "README_DG4_旧版兼容目录_请勿删除.txt"
    try:
        note.write_text(
            "这是 Chromatic Analysis 旧版本 Customers 兼容目录。\n\n"
            "DG4 当前正式业务数据源：ProgramData\\Chromatic Analysis\\Database\\color_data.sqlite3\n"
            "DG4 当前可读正式色库镜像：Documents\\Chromatic Analysis Data\\01 正式色库\n\n"
            "本目录不再作为正式色库的写入/读取主数据源，但旧版本记录、收藏或历史路径可能仍把这里的文件路径作为逻辑标识。\n"
            "因此程序保留该目录用于兼容与回滚，请不要手工删除。首次归档副本位于 99 旧版迁移源。\n",
            encoding="utf-8",
        )
    except Exception:
        pass
    report["hidden_on_windows"] = _mark_windows_hidden(old_root)
    report["action"] = "archived_and_compatibility_retained"
    return report


def _legacy_color_card_snapshot_dirs() -> list[Path]:
    root = color_card_root()
    result = []
    for child in root.iterdir() if root.exists() else []:
        if child.is_dir() and re.match(r"^\d{4}_.+", child.name):
            result.append(child)
    return result


def _human_color_card_snapshot_count() -> int:
    roots = [color_card_root() / x for x in ("共享方案", "用户方案", "已发布方案", "历史未归属")]
    return sum(1 for root in roots if root.exists() for p in root.rglob("*.chromaticcard.json") if p.is_file())


def _color_card_db_count(color_db: Path | None = None) -> int:
    path = Path(color_db or color_database_path())
    if not path.exists():
        return 0
    try:
        with sqlite3.connect(path) as db:
            return int(db.execute("SELECT COUNT(*) FROM color_cards").fetchone()[0] or 0)
    except Exception:
        return 0


def finalize_dg4_layout(color_db: Path | None = None, security_db: Path | None = None) -> dict:
    """One-time DG4 v2 finalisation: readable card folders + legacy safeguards."""
    ensure_business_layout(); ensure_system_layout()
    cfg = _read_config(); cfg["layout_version"] = DG4_LAYOUT_VERSION; _write_config(cfg)
    color_db = Path(color_db or color_database_path())
    security_db = Path(security_db or security_database_path())
    marker = ensure_system_layout() / "System" / f"dg4_layout_v{DG4_LAYOUT_VERSION}_finalize.json"
    if marker.exists():
        try:
            raw = json.loads(marker.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw
        except Exception:
            pass

    report = {"created_at": time.time(), "layout_version": DG4_LAYOUT_VERSION}
    snapshots = rebuild_readable_snapshots(color_db, security_db)
    report.update({"color_card_snapshots": int(snapshots.get("color_cards", 0)),
                   "workbench_snapshots": int(snapshots.get("workbenches", 0))})
    expected_cards = _color_card_db_count(color_db)
    actual_cards = _human_color_card_snapshot_count()
    report["expected_color_cards"] = expected_cards
    report["actual_color_card_snapshots"] = actual_cards

    # Archive the HF139 machine-ID folder layout only after the new readable
    # mirrors have been rebuilt and count-checked. No authoritative SQLite data
    # is touched by this operation.
    archived_card_dirs = []
    if actual_cards >= expected_cards:
        legacy_card_root = legacy_archive_root() / "色卡编排_旧结构"
        legacy_card_root.mkdir(parents=True, exist_ok=True)
        for old_dir in _legacy_color_card_snapshot_dirs():
            target = legacy_card_root / old_dir.name
            if target.exists():
                target = legacy_card_root / f"{old_dir.name}_{int(time.time())}"
            try:
                shutil.move(str(old_dir), str(target))
                archived_card_dirs.append(str(target))
            except Exception:
                pass
    report["archived_color_card_dirs"] = archived_card_dirs
    report["legacy_customers"] = archive_legacy_customers_snapshot(color_db)
    marker.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def migrate_business_data_root(new_root: str | Path) -> dict:
    new_root = Path(new_root).expanduser()
    old_root = business_data_root().resolve()
    try:
        new_resolved = new_root.resolve()
    except Exception:
        new_resolved = new_root.absolute()
    if new_resolved == old_root:
        return {"old_root": str(old_root), "new_root": str(new_resolved), "files": 0, "changed": False}
    if old_root in new_resolved.parents:
        raise ValueError("新的业务数据目录不能放在当前业务数据目录内部")
    if not _can_use_directory(new_resolved):
        raise PermissionError("目标目录不可写")
    staging = new_resolved
    staging.mkdir(parents=True, exist_ok=True)
    count = _copy_tree_merge(old_root, staging)
    # Verify count/size at a coarse level. We deliberately do not delete the old
    # tree; it remains a rollback copy until the user chooses to clean it up.
    src_files = [p for p in old_root.rglob("*") if p.is_file()]
    missing = []
    for src in src_files:
        dst = staging / src.relative_to(old_root)
        if not dst.exists() or dst.stat().st_size != src.stat().st_size:
            missing.append(str(src.relative_to(old_root)))
            if len(missing) >= 10:
                break
    if missing:
        raise IOError("业务数据迁移校验失败：" + ", ".join(missing))
    cfg = _read_config()
    cfg["business_root"] = str(new_resolved)
    cfg["layout_version"] = DG4_LAYOUT_VERSION
    cfg["business_root_changed_at"] = time.time()
    cfg["previous_business_root"] = str(old_root)
    _write_config(cfg)
    ensure_business_layout(new_resolved)
    return {"old_root": str(old_root), "new_root": str(new_resolved), "files": count, "changed": True}


def storage_health_report() -> dict:
    ensure_business_layout(); ensure_system_layout()
    dbs = {
        "security": security_database_path(),
        "color": color_database_path(),
        "audit": audit_database_path(),
    }
    db_report = {}
    for key, path in dbs.items():
        item = {"path": str(path), "exists": path.exists(), "ok": False, "message": ""}
        if path.exists():
            try:
                with sqlite3.connect(path) as db:
                    row = db.execute("PRAGMA quick_check").fetchone()
                item["ok"] = bool(row and str(row[0]).lower() == "ok")
                item["message"] = str(row[0] if row else "unknown")
            except Exception as exc:
                item["message"] = str(exc)
        db_report[key] = item
    valid_users: set[int] = {0}
    try:
        with sqlite3.connect(security_database_path()) as db:
            valid_users.update(int(r[0]) for r in db.execute("SELECT id FROM users").fetchall())
    except Exception:
        pass
    orphan_owners: set[int] = set()
    try:
        with sqlite3.connect(color_database_path()) as db:
            for table in ("color_cards", "workbenches"):
                try:
                    rows=db.execute(f"SELECT DISTINCT owner_user_id FROM {table} WHERE owner_user_id<>0").fetchall()
                    orphan_owners.update(int(r[0]) for r in rows if int(r[0]) not in valid_users)
                except sqlite3.DatabaseError:
                    pass
    except Exception:
        pass
    expected_cards = _color_card_db_count(color_database_path())
    actual_cards = _human_color_card_snapshot_count()
    card_governance = {"private":0,"organization":0,"legacy_unassigned":0,"published":0}
    try:
        with sqlite3.connect(color_database_path()) as db:
            cols={r[1] for r in db.execute("PRAGMA table_info(color_cards)").fetchall()}
            if "visibility" in cols:
                for key,count in db.execute("SELECT visibility,COUNT(*) FROM color_cards GROUP BY visibility").fetchall():
                    card_governance[str(key or 'private')]=int(count or 0)
            if "lifecycle_status" in cols:
                card_governance["published"]=int(db.execute("SELECT COUNT(*) FROM color_cards WHERE lifecycle_status='published'").fetchone()[0] or 0)
    except Exception:
        pass
    machine_dirs = [p.name for p in _legacy_color_card_snapshot_dirs()]
    legacy = legacy_customers_status(color_database_path())
    return {
        "layout_version": DG4_LAYOUT_VERSION,
        "business_root": str(business_data_root()),
        "system_root": str(system_data_root()),
        "databases": db_report,
        "formal_library_files": sum(1 for p in formal_library_root().rglob("*.qtx") if p.is_file()),
        "official_library_files": sum(1 for p in official_library_root().rglob("*.qtx") if p.is_file()),
        "color_card_files": actual_cards,
        "expected_color_cards": expected_cards,
        "color_card_snapshot_complete": actual_cards >= expected_cards,
        "color_card_governance": card_governance,
        "legacy_color_card_dirs": machine_dirs,
        "personal_files": sum(1 for p in personal_workspace_root().rglob("*") if p.is_file()),
        "orphan_owner_ids": sorted(orphan_owners),
        "legacy_customers": legacy,
    }

