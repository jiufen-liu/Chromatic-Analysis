from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QTreeWidget, QTreeWidgetItem, QHeaderView, QAbstractItemView, QGroupBox,
    QSplitter, QPlainTextEdit, QFrame,
    QVBoxLayout, QWidget,
)

from .audit_store import AuditStore
from .storage_v2 import migrate_legacy_security_database, security_database_path


PBKDF2_ROUNDS = 240_000
FEATURES = {
    'library_view': '色库浏览',
    'find': '查色 / 找色',
    'compare': '比色工作台',
    'cards': '色卡编排',
    'spectrum': '光谱分析',
    'export': '文件导出（QTX / CPX / Excel）',
    'workfile': '保存个人工作文件',
}


@dataclass(frozen=True)
class AuthUser:
    user_id: int
    username: str
    display_name: str
    role: str
    enabled: bool = True

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


class AuthStore:
    """Local RBAC store for the standalone desktop application.

    Passwords are never stored in clear text.  The application keeps a salted
    PBKDF2-SHA256 digest in a dedicated local SQLite database.  This is not a
    replacement for an enterprise identity provider, but it provides the role
    isolation required by an offline single-PC workflow.
    """

    def __init__(self) -> None:
        # DG4: identity/permissions are physically separated from colour data
        # and audit history. The legacy auth DB is copied once and retained as
        # a rollback source; only the DG4 copy is used afterwards.
        migrate_legacy_security_database()
        self.path = security_database_path()
        self._create_schema()
        self.audit_store = AuditStore()
        self.audit_store.migrate_from_security_database(self.path)

    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    def _create_schema(self) -> None:
        with self.connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    display_name TEXT NOT NULL DEFAULT '',
                    role TEXT NOT NULL CHECK(role IN ('operator','admin')),
                    password_salt TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,
                    last_login REAL
                )"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS organization_profile (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    name TEXT NOT NULL DEFAULT 'Chromatic Analysis 本地组织',
                    recovery_salt TEXT NOT NULL DEFAULT '',
                    recovery_hash TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL DEFAULT 0
                )"""
            )
            org_cols={row[1] for row in db.execute("PRAGMA table_info(organization_profile)").fetchall()}
            if 'recovery_salt' not in org_cols:
                db.execute("ALTER TABLE organization_profile ADD COLUMN recovery_salt TEXT NOT NULL DEFAULT ''")
            if 'recovery_hash' not in org_cols:
                db.execute("ALTER TABLE organization_profile ADD COLUMN recovery_hash TEXT NOT NULL DEFAULT ''")
            org=db.execute("SELECT id FROM organization_profile WHERE id=1").fetchone()
            if org is None:
                now=time.time()
                db.execute("INSERT INTO organization_profile(id,name,recovery_salt,recovery_hash,created_at,updated_at) VALUES(1,?,?,?,?,?)",
                           ('Chromatic Analysis 本地组织','','',now,now))
            db.execute("""CREATE TABLE IF NOT EXISTS user_features (
                user_id INTEGER NOT NULL, feature TEXT NOT NULL, allowed INTEGER NOT NULL,
                PRIMARY KEY(user_id, feature), FOREIGN KEY(user_id) REFERENCES users(id)
            )""")
            # DG-1: data-scope ACL foundation.  Hotfix55 creates the schema and
            # management API only; visibility enforcement starts in DG-2 so the
            # migration itself cannot hide existing data unexpectedly.
            db.execute("""CREATE TABLE IF NOT EXISTS data_scope_grants (
                user_id INTEGER NOT NULL,
                scope_type TEXT NOT NULL,
                scope_key TEXT NOT NULL,
                can_view INTEGER NOT NULL DEFAULT 1,
                can_export INTEGER NOT NULL DEFAULT 0,
                granted_by TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL DEFAULT 0,
                PRIMARY KEY(user_id, scope_type, scope_key),
                FOREIGN KEY(user_id) REFERENCES users(id)
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_scope_grants_user ON data_scope_grants(user_id, scope_type)")

    def organization_name(self) -> str:
        with self.connect() as db:
            row=db.execute("SELECT name FROM organization_profile WHERE id=1").fetchone()
        return str(row[0] if row else 'Chromatic Analysis 本地组织')

    def set_organization_name(self, name: str, *, changed_by: str = '') -> str:
        name=str(name or '').strip()
        if not name:
            raise ValueError('组织名称不能为空')
        now=time.time()
        with self.connect() as db:
            db.execute("INSERT INTO organization_profile(id,name,created_at,updated_at) VALUES(1,?,?,?) "
                       "ON CONFLICT(id) DO UPDATE SET name=excluded.name,updated_at=excluded.updated_at",
                       (name,now,now))
        if changed_by:
            self.log(changed_by,'SET_ORGANIZATION_NAME',name,'')
        return name

    @staticmethod
    def _normalize_recovery_key(value: str) -> str:
        return ''.join(ch for ch in str(value or '').upper() if ch.isalnum())

    def generate_recovery_key(self, *, changed_by: str = '') -> str:
        raw=secrets.token_hex(16).upper()
        key='-'.join(raw[i:i+4] for i in range(0,len(raw),4))
        salt=secrets.token_bytes(16)
        normalized=self._normalize_recovery_key(key)
        digest=hashlib.pbkdf2_hmac('sha256', normalized.encode('ascii'), salt, PBKDF2_ROUNDS)
        with self.connect() as db:
            db.execute("UPDATE organization_profile SET recovery_salt=?,recovery_hash=?,updated_at=? WHERE id=1",
                       (salt.hex(),digest.hex(),time.time()))
        if changed_by:
            self.log(changed_by,'ROTATE_ORGANIZATION_RECOVERY_KEY',self.organization_name(),'')
        return key

    def has_recovery_key(self) -> bool:
        with self.connect() as db:
            row=db.execute("SELECT recovery_salt,recovery_hash FROM organization_profile WHERE id=1").fetchone()
        return bool(row and row[0] and row[1])

    def verify_recovery_key(self, value: str) -> bool:
        normalized=self._normalize_recovery_key(value)
        with self.connect() as db:
            row=db.execute("SELECT recovery_salt,recovery_hash FROM organization_profile WHERE id=1").fetchone()
        if not row or not row[0] or not row[1] or not normalized:
            return False
        try:
            salt=bytes.fromhex(str(row[0])); expected=str(row[1])
            actual=hashlib.pbkdf2_hmac('sha256',normalized.encode('ascii'),salt,PBKDF2_ROUNDS).hex()
            return hmac.compare_digest(actual,expected)
        except Exception:
            return False

    def recover_admin_password(self, recovery_key: str, admin_user_id: int, new_password: str) -> None:
        if not self.verify_recovery_key(recovery_key):
            raise ValueError('组织恢复密钥无效')
        with self.connect() as db:
            row=db.execute("SELECT username,role FROM users WHERE id=?",(int(admin_user_id),)).fetchone()
        if row is None or str(row['role'])!='admin':
            raise ValueError('请选择管理者账号')
        self.reset_password(int(admin_user_id),new_password)
        self.log('recovery','RECOVER_ADMIN_PASSWORD',str(row['username']),'organization_recovery_key')

    def allowed_features(self, user: AuthUser) -> dict[str, bool]:
        if user.is_admin: return {key: True for key in FEATURES}
        with self.connect() as db:
            rows=db.execute('SELECT feature,allowed FROM user_features WHERE user_id=?',(user.user_id,)).fetchall()
        permissions={key:True for key in FEATURES}  # Existing users retain existing access.
        permissions.update({row['feature']:bool(row['allowed']) for row in rows if row['feature'] in FEATURES})
        return permissions

    def can_use(self, user: AuthUser, feature: str) -> bool:
        return self.allowed_features(user).get(feature,False)

    def set_features(self, user_id: int, features: dict[str,bool]) -> None:
        with self.connect() as db:
            role=db.execute('SELECT role FROM users WHERE id=?',(int(user_id),)).fetchone()
            if role is None:raise ValueError('账户不存在')
            if role['role']=='admin':raise ValueError('管理者账户保留完整管理权限')
            for key, allowed in features.items():
                if key not in FEATURES:raise ValueError('未知功能：'+key)
                db.execute('INSERT OR REPLACE INTO user_features(user_id,feature,allowed) VALUES(?,?,?)',
                           (int(user_id),key,int(bool(allowed))))

    def copy_user_permissions(self, source_user_id: int, target_user_id: int, *, granted_by: str = "") -> None:
        """Copy feature and data-scope permissions between operator accounts."""
        users={u.user_id:u for u in self.list_users()}
        source=users.get(int(source_user_id)); target=users.get(int(target_user_id))
        if source is None or target is None:
            raise ValueError('账户不存在')
        if source.is_admin or target.is_admin:
            raise ValueError('管理员权限固定，不能作为普通使用者权限复制的来源或目标')
        self.set_features(target.user_id, self.allowed_features(source))
        if not self.has_scope_grants(source.user_id):
            self.clear_scope_policy(target.user_id)
            return
        policy=self.data_scope_policy(source)
        officials={k:(bool(v.get('view')),bool(v.get('export'))) for k,v in (policy.get('officials') or {}).items()}
        customers={k:(bool(v.get('view')),bool(v.get('export'))) for k,v in (policy.get('customers') or {}).items()}
        self.replace_scope_policy(target.user_id, official_view=bool(policy.get('official_view')), official_export=bool(policy.get('official_export')), official_grants=officials, formal_all_view=bool(policy.get('formal_all_view')), formal_all_export=bool(policy.get('formal_all_export')), customer_grants=customers, granted_by=granted_by)

    def list_scope_grants(self, user_id: int | None = None) -> list[sqlite3.Row]:
        """Return DG-1 data-scope grants without enforcing them yet."""
        with self.connect() as db:
            if user_id is None:
                return db.execute(
                    "SELECT user_id,scope_type,scope_key,can_view,can_export,granted_by,created_at,updated_at "
                    "FROM data_scope_grants ORDER BY user_id,scope_type,scope_key"
                ).fetchall()
            return db.execute(
                "SELECT user_id,scope_type,scope_key,can_view,can_export,granted_by,created_at,updated_at "
                "FROM data_scope_grants WHERE user_id=? ORDER BY scope_type,scope_key",
                (int(user_id),),
            ).fetchall()

    def set_scope_grant(self, user_id: int, scope_type: str, scope_key: str, *,
                        can_view: bool = True, can_export: bool = False, granted_by: str = "") -> None:
        """Create/update one future RBAC 2.0 data-scope grant.

        DG-1 intentionally does not wire this into library queries.  DG-2 will
        enforce these rows after the administrator has an explicit management UI.
        """
        scope_type = str(scope_type or '').strip().lower()
        scope_key = str(scope_key or '').replace('\\','/').strip('/')
        if scope_type not in {'official', 'formal', 'customer'}:
            raise ValueError('未知数据范围类型')
        if scope_type == 'customer' and not scope_key:
            raise ValueError('客户数据范围不能为空')
        now=time.time()
        with self.connect() as db:
            user=db.execute('SELECT id FROM users WHERE id=?',(int(user_id),)).fetchone()
            if user is None:
                raise ValueError('账户不存在')
            db.execute(
                """INSERT INTO data_scope_grants(user_id,scope_type,scope_key,can_view,can_export,granted_by,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(user_id,scope_type,scope_key) DO UPDATE SET
                     can_view=excluded.can_view,can_export=excluded.can_export,
                     granted_by=excluded.granted_by,updated_at=excluded.updated_at""",
                (int(user_id),scope_type,scope_key,int(bool(can_view)),int(bool(can_export)),
                 str(granted_by or ''),now,now),
            )

    def remove_scope_grant(self, user_id: int, scope_type: str, scope_key: str) -> None:
        with self.connect() as db:
            db.execute('DELETE FROM data_scope_grants WHERE user_id=? AND scope_type=? AND scope_key=?',
                       (int(user_id),str(scope_type or '').strip().lower(),str(scope_key or '').replace('\\','/').strip('/')))

    def has_scope_grants(self, user_id: int) -> bool:
        with self.connect() as db:
            return db.execute('SELECT 1 FROM data_scope_grants WHERE user_id=? LIMIT 1',(int(user_id),)).fetchone() is not None

    @staticmethod
    def _scope_key(value: str) -> str:
        return str(value or '').replace('\\','/').strip('/')

    def data_scope_policy(self, user: AuthUser) -> dict:
        """Return the effective DG-2.1 resource policy.

        Resource rules are hierarchical.  A root rule applies to descendants; a
        more-specific rule overrides its parent.  Absence of a child row means
        inheritance.  Existing pre-DG2 users without any rows remain in legacy
        compatibility mode until an administrator saves the unified permission
        center.
        """
        if user.is_admin:
            return {
                'configured': True, 'unrestricted': True,
                'official_view': True, 'official_export': True,
                'formal_all_view': True, 'formal_all_export': True,
                'officials': {}, 'customers': {},
            }
        rows=self.list_scope_grants(user.user_id)
        if not rows:
            return {
                'configured': False, 'unrestricted': True,
                'official_view': True, 'official_export': self.can_use(user,'export'),
                'formal_all_view': True, 'formal_all_export': self.can_use(user,'export'),
                'officials': {}, 'customers': {},
            }
        policy={
            'configured': True, 'unrestricted': False,
            'official_view': False, 'official_export': False,
            'formal_all_view': False, 'formal_all_export': False,
            'officials': {}, 'customers': {},
        }
        for row in rows:
            typ=str(row['scope_type'] or '').strip().lower(); key=self._scope_key(row['scope_key'])
            view=bool(row['can_view']); export=bool(row['can_export']) and view
            if typ=='official':
                if key in {'','*','官方色库'}:
                    policy['official_view']=view; policy['official_export']=export
                else:
                    if key.startswith('官方色库/'):
                        key=key.split('/',1)[1]
                    policy['officials'][key]={'view':view,'export':export}
            elif typ=='formal' and key in {'','*','正式色库'}:
                policy['formal_all_view']=view; policy['formal_all_export']=export
            elif typ=='customer' and key:
                if key.startswith('正式色库/'):
                    key=key.split('/',1)[1]
                policy['customers'][key]={'view':view,'export':export}
        return policy

    @staticmethod
    def _longest_scope_match(path: str, grants: dict, base_view: bool, base_export: bool=False):
        path=str(path or '').replace('\\','/').strip('/')
        best=None
        for prefix, grant in (grants or {}).items():
            prefix=str(prefix or '').replace('\\','/').strip('/')
            if not prefix:
                continue
            if path==prefix or path.startswith(prefix.rstrip('/')+'/'):
                rank=len(prefix)
                if best is None or rank>best[0]:
                    best=(rank,bool(grant.get('view')),bool(grant.get('export')))
        if best is None:
            return bool(base_view), bool(base_view and base_export)
        return bool(best[1]), bool(best[1] and best[2])

    def can_view_customer(self, user: AuthUser, customer: str) -> bool:
        """Data access only. Feature gates are evaluated by the calling action.

        This distinction is intentional: a production user may be denied the
        *library browser* while still being allowed to use an authorised Coloro
        or customer library as the candidate pool for Find/Compare.
        """
        customer=self._scope_key(customer)
        policy=self.data_scope_policy(user)
        if policy.get('unrestricted'):
            return True
        if customer=='官方色库' or customer.startswith('官方色库/'):
            relative=customer.split('/',1)[1] if '/' in customer else ''
            view,_export=self._longest_scope_match(relative,policy.get('officials',{}),
                                                    policy.get('official_view'),policy.get('official_export'))
            return view
        relative=customer.split('/',1)[1] if customer.startswith('正式色库/') else customer
        view,_export=self._longest_scope_match(relative,policy.get('customers',{}),
                                                policy.get('formal_all_view'),policy.get('formal_all_export'))
        return view

    def can_export_customer(self, user: AuthUser, customer: str) -> bool:
        # Global export is a hard capability gate: a data-scope row can never
        # re-enable an action disabled by the feature ACL.
        if not self.can_use(user,'export'):
            return False
        customer=self._scope_key(customer)
        policy=self.data_scope_policy(user)
        if policy.get('unrestricted'):
            return True
        if customer=='官方色库' or customer.startswith('官方色库/'):
            relative=customer.split('/',1)[1] if '/' in customer else ''
            view,export=self._longest_scope_match(relative,policy.get('officials',{}),
                                                   policy.get('official_view'),policy.get('official_export'))
            return bool(view and export)
        relative=customer.split('/',1)[1] if customer.startswith('正式色库/') else customer
        view,export=self._longest_scope_match(relative,policy.get('customers',{}),
                                               policy.get('formal_all_view'),policy.get('formal_all_export'))
        return bool(view and export)

    def visible_customer_prefixes(self, user: AuthUser) -> list[str] | None:
        """Return explicit visible formal prefixes, or None when root allows all."""
        policy=self.data_scope_policy(user)
        if policy.get('unrestricted') or policy.get('formal_all_view'):
            return None
        return sorted([k for k,v in policy.get('customers',{}).items() if v.get('view')], key=str.casefold)

    def visible_official_prefixes(self, user: AuthUser) -> list[str] | None:
        policy=self.data_scope_policy(user)
        if policy.get('unrestricted') or policy.get('official_view'):
            return None
        return sorted([k for k,v in policy.get('officials',{}).items() if v.get('view')], key=str.casefold)

    def replace_scope_policy(self, user_id: int, *, official_view: bool, official_export: bool,
                             official_grants: dict[str, tuple[bool,bool]] | None = None,
                             customer_grants: dict[str, tuple[bool,bool]] | None = None,
                             formal_all_view: bool = False, formal_all_export: bool = False,
                             granted_by: str = '') -> None:
        """Atomically replace one operator's hierarchical resource policy.

        Root rows are always written.  Child rows are written only when they
        differ from the inherited parent, so newly added libraries naturally
        inherit their branch policy.
        """
        with self.connect() as db:
            row=db.execute('SELECT role FROM users WHERE id=?',(int(user_id),)).fetchone()
            if row is None: raise ValueError('账户不存在')
            if row['role']=='admin': raise ValueError('管理者始终拥有全部数据范围')
            db.execute('DELETE FROM data_scope_grants WHERE user_id=?',(int(user_id),))
            now=time.time(); who=str(granted_by or '')
            rows=[
                (int(user_id),'official','*',int(bool(official_view)),int(bool(official_view and official_export)),who,now,now),
                (int(user_id),'formal','*',int(bool(formal_all_view)),int(bool(formal_all_view and formal_all_export)),who,now,now),
            ]
            official_normalized={}
            for key,(view,export) in (official_grants or {}).items():
                key=self._scope_key(key)
                if key.startswith('官方色库/'):
                    key=key.split('/',1)[1]
                if key: official_normalized[key]=(bool(view),bool(view and export))
            for key in sorted(official_normalized,key=lambda x:(x.count('/'),x.casefold())):
                state=official_normalized[key]
                parent=key.rsplit('/',1)[0] if '/' in key else ''
                inherited=(bool(official_view),bool(official_view and official_export))
                while parent:
                    if parent in official_normalized:
                        inherited=official_normalized[parent]; break
                    parent=parent.rsplit('/',1)[0] if '/' in parent else ''
                if state==inherited: continue
                rows.append((int(user_id),'official',key,int(state[0]),int(state[1]),who,now,now))
            # For nested formal resources, compare with the nearest already
            # supplied parent state so identical children are stored as inherit.
            normalized={}
            for key,(view,export) in (customer_grants or {}).items():
                key=self._scope_key(key)
                if key.startswith('正式色库/'):
                    key=key.split('/',1)[1]
                if key: normalized[key]=(bool(view),bool(view and export))
            for key in sorted(normalized,key=lambda x:(x.count('/'),x.casefold())):
                state=normalized[key]
                parent=key.rsplit('/',1)[0] if '/' in key else ''
                inherited=(bool(formal_all_view),bool(formal_all_view and formal_all_export))
                while parent:
                    if parent in normalized:
                        inherited=normalized[parent]; break
                    parent=parent.rsplit('/',1)[0] if '/' in parent else ''
                if state==inherited: continue
                rows.append((int(user_id),'customer',key,int(state[0]),int(state[1]),who,now,now))
            db.executemany(
                'INSERT INTO data_scope_grants(user_id,scope_type,scope_key,can_view,can_export,granted_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                rows,
            )

    def clear_scope_policy(self, user_id: int) -> None:
        """Return a legacy operator to compatibility mode (administrator-only UI)."""
        with self.connect() as db:
            db.execute('DELETE FROM data_scope_grants WHERE user_id=?',(int(user_id),))

    @staticmethod
    def _hash_password(password: str, salt_hex: str | None = None) -> tuple[str, str]:
        salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
        return salt.hex(), digest.hex()

    def user_count(self) -> int:
        with self.connect() as db:
            return int(db.execute("SELECT COUNT(*) FROM users").fetchone()[0])

    def create_user(self, username: str, password: str, role: str, display_name: str = "") -> AuthUser:
        username = username.strip()
        display_name = display_name.strip() or username
        if not username:
            raise ValueError("账号不能为空")
        if len(password) < 6:
            raise ValueError("密码至少 6 位")
        role = "admin" if role == "admin" else "operator"
        salt, digest = self._hash_password(password)
        with self.connect() as db:
            cur = db.execute(
                "INSERT INTO users(username,display_name,role,password_salt,password_hash,enabled,created_at) VALUES(?,?,?,?,?,?,?)",
                (username, display_name, role, salt, digest, 1, time.time()),
            )
            uid = int(cur.lastrowid)
        # DG-2 secure default for newly created operators: public/official
        # libraries are visible, while company formal libraries remain hidden
        # until an administrator grants customer scope explicitly. Existing
        # pre-DG2 operators keep compatibility mode until configured.
        if role == 'operator':
            self.replace_scope_policy(uid, official_view=True, official_export=False,
                                      customer_grants={}, granted_by=username)
        self.log(username, "CREATE_USER", username, role)
        return AuthUser(uid, username, display_name, role, True)

    def authenticate(self, username: str, password: str) -> AuthUser | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM users WHERE username=?", (username.strip(),)).fetchone()
            if not row or not bool(row["enabled"]):
                return None
            _, digest = self._hash_password(password, row["password_salt"])
            if not hmac.compare_digest(digest, row["password_hash"]):
                return None
            db.execute("UPDATE users SET last_login=? WHERE id=?", (time.time(), row["id"]))
            user = AuthUser(int(row["id"]), row["username"], row["display_name"], row["role"], True)
        self.log(user.username, "LOGIN", "", "")
        return user

    def list_users(self) -> list[AuthUser]:
        with self.connect() as db:
            rows = db.execute("SELECT id,username,display_name,role,enabled FROM users ORDER BY role DESC, username COLLATE NOCASE").fetchall()
        return [AuthUser(int(r["id"]), r["username"], r["display_name"], r["role"], bool(r["enabled"])) for r in rows]

    def set_enabled(self, user_id: int, enabled: bool) -> None:
        with self.connect() as db:
            db.execute("UPDATE users SET enabled=? WHERE id=?", (1 if enabled else 0, int(user_id)))

    def reset_password(self, user_id: int, password: str) -> None:
        if len(password) < 6:
            raise ValueError("密码至少 6 位")
        salt, digest = self._hash_password(password)
        with self.connect() as db:
            db.execute("UPDATE users SET password_salt=?,password_hash=? WHERE id=?", (salt, digest, int(user_id)))

    def log(self, username: str, action: str, target: str = "", detail: str = "") -> None:
        self.audit_store.log(username, action, target, detail)

    def recent_log(self, limit: int = 300) -> list[sqlite3.Row]:
        return self.query_audit(limit=limit)

    def query_audit(
        self, *, since: float | None = None, username: str = "", action: str = "",
        keyword: str = "", limit: int | None = 5000,
    ) -> list[sqlite3.Row]:
        return self.audit_store.query(
            since=since, username=username, action=action, keyword=keyword, limit=limit
        )

    def audit_filter_values(self) -> tuple[list[str], list[str]]:
        return self.audit_store.filter_values()



class FirstRunAdminDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("首次运行 · 初始化组织与管理者")
        self.setMinimumWidth(440)
        root = QVBoxLayout(self)
        text = QLabel("首次运行需要先建立本机所属组织，并由客户自己创建首位管理者。\n开发者不会获得客户组织中的隐藏管理员账号；后续管理者由组织现有管理者创建。")
        text.setWordWrap(True)
        root.addWidget(text)
        form = QFormLayout()
        self.organization = QLineEdit()
        self.organization.setPlaceholderText("例如：ABC Textile")
        self.user = QLineEdit("admin")
        self.name = QLineEdit("Administrator")
        self.password = QLineEdit(); self.password.setEchoMode(QLineEdit.Password)
        self.password2 = QLineEdit(); self.password2.setEchoMode(QLineEdit.Password)
        form.addRow("组织 / 公司", self.organization)
        form.addRow("管理员账号", self.user); form.addRow("显示名称", self.name)
        form.addRow("密码", self.password); form.addRow("确认密码", self.password2)
        root.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("创建并登录")
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def values(self):
        return (self.organization.text().strip(), self.user.text().strip(), self.password.text(),
                self.password2.text(), self.name.text().strip())


class LoginDialog(QDialog):
    def __init__(self, auth: AuthStore, parent=None):
        super().__init__(parent)
        self.auth = auth; self.result_user: AuthUser | None = None
        self.setWindowTitle("Chromatic Analysis · 登录")
        self.setMinimumWidth(430)
        self.setObjectName("loginDialog")
        root = QVBoxLayout(self); root.setContentsMargins(28,26,28,24); root.setSpacing(14)
        brand = QLabel("Chromatic Analysis"); brand.setObjectName("loginBrand")
        root.addWidget(brand)
        tip = QLabel("选择本机账号后输入密码。角色由账号决定。")
        tip.setObjectName("loginTip"); tip.setWordWrap(True); root.addWidget(tip)
        form = QFormLayout(); form.setHorizontalSpacing(14); form.setVerticalSpacing(12)
        self.user = QComboBox(); self.user.setObjectName("loginUser")
        for account in self.auth.list_users():
            if not account.enabled:
                continue
            role = "管理者" if account.is_admin else "使用者"
            self.user.addItem(f"{account.username}  ·  {role}", account.username)
        self.password = QLineEdit(); self.password.setEchoMode(QLineEdit.Password); self.password.setPlaceholderText("输入密码")
        form.addRow("账号", self.user); form.addRow("密码", self.password); root.addLayout(form)
        self.message = QLabel(""); self.message.setObjectName("loginError"); root.addWidget(self.message)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("登录")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._login); buttons.rejected.connect(self.reject)
        recovery = QPushButton("管理员恢复…")
        recovery.clicked.connect(self._recover_admin)
        root.addWidget(recovery)
        root.addWidget(buttons)
        self.password.returnPressed.connect(self._login)
        self.setStyleSheet("""
            QDialog#loginDialog{background:#F3F4F6;color:#171A1F;font-family:'Microsoft YaHei UI','Segoe UI';}
            QLabel#loginBrand{font-size:24px;font-weight:800;color:#171A1F;margin-bottom:2px;}
            QLabel#loginTip{color:#737983;font-size:12px;margin-bottom:6px;}
            QLabel#loginError{color:#B42332;min-height:18px;}
            QComboBox,QLineEdit{background:#FFFFFF;border:1px solid #D5D9DF;border-radius:9px;padding:7px 10px;min-height:25px;color:#24282E;}
            QComboBox:focus,QLineEdit:focus{border:1px solid #A67C45;}
            QPushButton{background:#FFFFFF;color:#252A31;border:1px solid #D7DBE1;border-radius:9px;padding:7px 18px;min-width:72px;}
            QPushButton:hover{background:#F8F9FA;}
        """)
        ok_button=buttons.button(QDialogButtonBox.Ok)
        ok_button.setStyleSheet("background:#171A1F;color:white;border:1px solid #171A1F;border-radius:9px;padding:7px 20px;font-weight:650;")
        if self.user.count():
            self.password.setFocus()

    def _recover_admin(self):
        admins=[u for u in self.auth.list_users() if u.is_admin and u.enabled]
        if not admins:
            QMessageBox.warning(self,'管理员恢复','当前没有可恢复的管理者账号。')
            return
        if not self.auth.has_recovery_key():
            QMessageBox.information(self,'管理员恢复','当前组织尚未设置恢复密钥。请使用仍可登录的管理者在“本地数据管理”中生成。')
            return
        dlg=QDialog(self); dlg.setWindowTitle('组织管理员恢复')
        form=QFormLayout(dlg)
        account=QComboBox()
        for u in admins: account.addItem(f'{u.display_name or u.username} ({u.username})',u.user_id)
        key=QLineEdit(); key.setPlaceholderText('XXXX-XXXX-...')
        pw=QLineEdit(); pw.setEchoMode(QLineEdit.Password)
        pw2=QLineEdit(); pw2.setEchoMode(QLineEdit.Password)
        form.addRow('管理者账号',account); form.addRow('组织恢复密钥',key); form.addRow('新密码',pw); form.addRow('确认密码',pw2)
        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel); buttons.accepted.connect(dlg.accept); buttons.rejected.connect(dlg.reject); form.addRow(buttons)
        if dlg.exec()!=QDialog.Accepted:return
        if pw.text()!=pw2.text():
            QMessageBox.warning(self,'管理员恢复','两次输入的新密码不一致。'); return
        try:
            self.auth.recover_admin_password(key.text(),int(account.currentData()),pw.text())
            QMessageBox.information(self,'管理员恢复','管理者密码已重置。请使用新密码登录。')
        except Exception as exc:
            QMessageBox.warning(self,'管理员恢复',str(exc))

    def _login(self):
        username = self.user.currentData() if self.user.count() else ""
        user = self.auth.authenticate(str(username or ""), self.password.text())
        if user is None:
            self.message.setText("账号或密码错误，或账号已停用。")
            return
        self.result_user = user
        self.accept()


class PermissionEditor(QWidget):
    """Hotfix58 compact permission editor.

    The UI intentionally exposes only two concepts: what the user can do and
    which managed data the user can use. There are no foreground role templates;
    the hierarchical ACL remains an implementation detail.
    """
    saved = Signal()

    def __init__(self, auth: AuthStore, current_user: AuthUser, resources: list[str], parent=None):
        super().__init__(parent)
        self.auth = auth
        self.current_user = current_user
        self.resources = sorted({AuthStore._scope_key(x) for x in resources if AuthStore._scope_key(x)}, key=str.casefold)
        self.user: AuthUser | None = None
        self._updating = False
        self._items = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        header = QFrame()
        header.setObjectName('permHeaderCard')
        hl = QHBoxLayout(header)
        hl.setContentsMargins(18, 14, 18, 14)
        self.avatar = QLabel('用')
        self.avatar.setObjectName('permAvatar')
        self.avatar.setAlignment(Qt.AlignCenter)
        self.avatar.setFixedSize(52, 52)
        hl.addWidget(self.avatar)
        htext = QVBoxLayout()
        htext.setSpacing(2)
        self.user_title = QLabel('请选择用户')
        self.user_title.setObjectName('permUserTitle')
        htext.addWidget(self.user_title)
        self.user_subtitle = QLabel('')
        self.user_subtitle.setObjectName('permMuted')
        htext.addWidget(self.user_subtitle)
        hl.addLayout(htext)
        hl.addStretch(1)
        self.copy_btn = QPushButton('复制其他用户权限…')
        self.copy_btn.setObjectName('secondaryButton')
        self.copy_btn.clicked.connect(self._copy_permissions)
        hl.addWidget(self.copy_btn)
        root.addWidget(header)

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        root.addWidget(split, 1)

        features = QFrame()
        features.setObjectName('permCard')
        fl = QVBoxLayout(features)
        fl.setContentsMargins(18, 16, 18, 16)
        fl.setSpacing(8)
        ft = QLabel('功能能力')
        ft.setObjectName('permSectionTitle')
        fl.addWidget(ft)
        fh = QLabel('决定该用户能进入哪些模块、执行哪些操作。')
        fh.setObjectName('permMuted')
        fl.addWidget(fh)
        fl.addSpacing(4)
        self.feature_checks = {}
        current_group = None
        module_keys = {'library_view', 'find', 'compare', 'cards', 'spectrum'}
        for key, label in FEATURES.items():
            group = '模块访问' if key in module_keys else '操作权限'
            if group != current_group:
                if current_group is not None:
                    line = QFrame()
                    line.setFrameShape(QFrame.HLine)
                    line.setObjectName('permDivider')
                    fl.addWidget(line)
                gl = QLabel(group)
                gl.setObjectName('permGroupLabel')
                fl.addWidget(gl)
                current_group = group
            cb = QCheckBox(label)
            cb.setObjectName('permCheck')
            cb.toggled.connect(self._refresh_effective_summary)
            fl.addWidget(cb)
            self.feature_checks[key] = cb
        fl.addStretch(1)
        split.addWidget(features)

        data = QFrame()
        data.setObjectName('permCard')
        dl = QVBoxLayout(data)
        dl.setContentsMargins(18, 16, 18, 16)
        dl.setSpacing(8)
        dt = QLabel('数据访问范围')
        dt.setObjectName('permSectionTitle')
        dl.addWidget(dt)
        dh = QLabel('官方色库和正式色库使用同一套规则。只授权当前用户真正需要的数据。')
        dh.setObjectName('permMuted')
        dh.setWordWrap(True)
        dl.addWidget(dh)
        self.tree = QTreeWidget()
        self.tree.setObjectName('permissionTree')
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(['数据资源', '可访问 / 使用', '可导出'])
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tree.setAlternatingRowColors(False)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.itemChanged.connect(self._item_changed)
        dl.addWidget(self.tree, 1)
        split.addWidget(data)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([340, 650])

        summary = QFrame()
        summary.setObjectName('permSummaryCard')
        sl = QVBoxLayout(summary)
        sl.setContentsMargins(18, 14, 18, 14)
        sl.setSpacing(5)
        st = QLabel('当前有效权限')
        st.setObjectName('permSectionTitle')
        sl.addWidget(st)
        self.summary = QLabel('请选择用户。')
        self.summary.setWordWrap(True)
        self.summary.setObjectName('permSummaryText')
        sl.addWidget(self.summary)
        root.addWidget(summary)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.save_btn = QPushButton('保存')
        self.save_btn.setObjectName('primaryButton')
        self.save_btn.setMinimumWidth(100)
        self.save_btn.clicked.connect(self.save)
        actions.addWidget(self.save_btn)
        root.addLayout(actions)
        self._set_editor_enabled(False)

    def _set_editor_enabled(self, enabled: bool):
        for cb in self.feature_checks.values():
            cb.setEnabled(enabled)
        self.tree.setEnabled(enabled)
        self.copy_btn.setEnabled(enabled)
        self.save_btn.setEnabled(enabled)

    def set_resources(self, resources: list[str]):
        self.resources = sorted({AuthStore._scope_key(x) for x in resources if AuthStore._scope_key(x)}, key=str.casefold)
        if self.user is not None and not self.user.is_admin:
            self._build_resource_tree()

    def load_user(self, user: AuthUser | None):
        self.user = user
        self._updating = True
        try:
            if user is None:
                self.user_title.setText('请选择用户')
                self.user_subtitle.setText('')
                self.avatar.setText('用')
                self._set_editor_enabled(False)
                self._items = {}
                _blocked = self.tree.blockSignals(True)
                try:
                    self.tree.clear()
                finally:
                    self.tree.blockSignals(_blocked)
                self.summary.setText('请选择左侧用户后设置权限。')
                return
            self.avatar.setText((user.display_name or user.username or '用')[:1].upper())
            self.user_title.setText(f'{user.display_name or user.username}  {user.username}')
            self.user_subtitle.setText(('管理员' if user.is_admin else '使用者') + (' · 启用' if user.enabled else ' · 已停用'))
            if user.is_admin:
                for cb in self.feature_checks.values():
                    cb.setChecked(True)
                self._items = {}
                _blocked = self.tree.blockSignals(True)
                try:
                    self.tree.clear()
                finally:
                    self.tree.blockSignals(_blocked)
                self._set_editor_enabled(False)
                self.copy_btn.hide()
                self.save_btn.hide()
                self.summary.setText('管理员固定拥有全部功能、全部官方色库、全部正式色库以及用户与数据管理权限。')
                return
            self.copy_btn.show()
            self.save_btn.show()
            self._set_editor_enabled(True)
            current = self.auth.allowed_features(user)
            for key, cb in self.feature_checks.items():
                cb.setChecked(bool(current.get(key)))
            self._build_resource_tree()
        finally:
            self._updating = False
        self._refresh_effective_summary()

    def _resource_state(self, path):
        if self.user is None:
            return False, False
        return bool(self.auth.can_view_customer(self.user, path)), bool(self.auth.can_export_customer(self.user, path))

    def _new_item(self, parent, label, path, view, export):
        item = QTreeWidgetItem([label, '', ''])
        item.setData(0, Qt.UserRole, path)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(1, Qt.Checked if view else Qt.Unchecked)
        item.setCheckState(2, Qt.Checked if export and view else Qt.Unchecked)
        if parent is None:
            self.tree.addTopLevelItem(item)
        else:
            parent.addChild(item)
        self._items[path] = item
        return item

    def _build_resource_tree(self):
        if self.user is None:
            return
        previous = self._updating
        self._updating = True
        # QTreeWidget.clear() deletes the underlying C++ QTreeWidgetItem objects
        # immediately.  Hotfix58 kept the old Python wrappers in self._items until
        # after clear(), while itemChanged/summary callbacks could still run.  That
        # left short-lived dangling shiboken wrappers and caused repeated
        # "Internal C++ object already deleted" exceptions.  Drop references
        # first and silence tree signals for the whole rebuild.
        blocked = self.tree.blockSignals(True)
        try:
            self._items = {}
            self.tree.clear()
            policy = self.auth.data_scope_policy(self.user)
            official_root = self._new_item(None, '官方色库', '官方色库', bool(policy.get('official_view')), bool(policy.get('official_export')))
            formal_root = self._new_item(None, '正式色库', '正式色库', bool(policy.get('formal_all_view')), bool(policy.get('formal_all_export')))
            roots = {'官方色库': official_root, '正式色库': formal_root}
            for full in self.resources:
                if full in {'官方色库', '正式色库'}:
                    continue
                scope = '官方色库' if full.startswith('官方色库/') else '正式色库'
                relative = full.split('/', 1)[1] if full.startswith(scope + '/') else full
                parent = roots[scope]
                acc = scope
                for part in [x for x in relative.split('/') if x]:
                    acc = acc + '/' + part
                    item = self._items.get(acc)
                    if item is None:
                        view, export = self._resource_state(acc)
                        item = self._new_item(parent, part, acc, view, export)
                    parent = item
            self.tree.expandToDepth(1)
        finally:
            self.tree.blockSignals(blocked)
            self._updating = previous

    def _set_descendants(self, item, column, state):
        for i in range(item.childCount()):
            child = item.child(i)
            child.setCheckState(column, state)
            if column == 1 and state != Qt.Checked:
                child.setCheckState(2, Qt.Unchecked)
            if column == 2 and state == Qt.Checked:
                child.setCheckState(1, Qt.Checked)
            self._set_descendants(child, column, state)

    def _item_changed(self, item, column):
        if self._updating:
            return
        self._updating = True
        try:
            if column == 1:
                if item.checkState(1) != Qt.Checked:
                    item.setCheckState(2, Qt.Unchecked)
                self._set_descendants(item, 1, item.checkState(1))
            elif column == 2:
                if item.checkState(2) == Qt.Checked:
                    item.setCheckState(1, Qt.Checked)
                self._set_descendants(item, 2, item.checkState(2))
        finally:
            self._updating = False
        self._refresh_effective_summary()

    def _tree_state(self, path):
        item = self._items.get(path)
        if item is None:
            return False, False
        try:
            view = item.checkState(1) == Qt.Checked
            export = item.checkState(2) == Qt.Checked and view
            return view, export
        except RuntimeError:
            # A queued signal can arrive just after Qt destroys old tree items.
            # Treat that obsolete row as unavailable instead of propagating an
            # exception into the GUI event loop.
            return False, False

    def _collect_branch(self, root_path):
        result = {}
        prefix = root_path + '/'
        for path, item in self._items.items():
            if path.startswith(prefix):
                result[path[len(prefix):]] = self._tree_state(path)
        return result

    def _refresh_effective_summary(self):
        if self.user is None or self.user.is_admin or self._updating:
            return
        enabled = [FEATURES[k] for k, c in self.feature_checks.items() if c.isChecked()]
        accessible = []
        exportable = []
        for path, item in list(self._items.items()):
            try:
                if item.childCount():
                    continue
            except RuntimeError:
                # Obsolete wrapper from a queued signal after a tree rebuild.
                continue
            view, export = self._tree_state(path)
            if view:
                accessible.append(path)
            if export:
                exportable.append(path)
        function_text = '、'.join(enabled) if enabled else '无'
        resources = ', '.join(x.replace('官方色库/', '').replace('正式色库/', '') for x in accessible[:8]) if accessible else '无'
        if len(accessible) > 8:
            resources += f' 等 {len(accessible)} 个'
        warnings = []
        if not self.feature_checks['library_view'].isChecked() and accessible:
            warnings.append('色库浏览关闭，但已授权数据仍可供查色/比色等已开启功能使用')
        if not self.feature_checks['export'].isChecked() and exportable:
            warnings.append('文件导出关闭，因此下方“可导出”授权当前不生效')
        text = f'<b>可用功能：</b>{function_text}<br><b>可访问数据：</b>{resources}'
        if warnings:
            text += '<br><span style="color:#6E7785">' + '；'.join(warnings) + '</span>'
        self.summary.setText(text)

    def _copy_permissions(self):
        if self.user is None or self.user.is_admin:
            return
        candidates = [u for u in self.auth.list_users() if not u.is_admin and u.user_id != self.user.user_id]
        if not candidates:
            QMessageBox.information(self, '复制权限', '当前没有其他普通使用者可作为权限来源。')
            return
        labels = [f'{u.display_name or u.username}  ({u.username})' for u in candidates]
        from PySide6.QtWidgets import QInputDialog
        picked, ok = QInputDialog.getItem(self, '复制其他用户权限', '选择权限来源：', labels, 0, False)
        if not ok:
            return
        src = candidates[labels.index(picked)]
        answer = QMessageBox.question(
            self, '复制权限',
            f'将 {src.display_name or src.username} 的功能权限和数据访问范围复制给 {self.user.display_name or self.user.username}？\n\n账号、姓名、密码和启用状态不会改变。'
        )
        if answer != QMessageBox.Yes:
            return
        try:
            self.auth.copy_user_permissions(src.user_id, self.user.user_id, granted_by=self.current_user.username)
            self.auth.log(self.current_user.username, 'COPY_USER_PERMISSIONS', self.user.username, f'from={src.username}')
            self.load_user(self.user)
        except Exception as exc:
            QMessageBox.warning(self, '复制权限', str(exc))

    def save(self):
        if self.user is None or self.user.is_admin:
            return False
        settings = {key: box.isChecked() for key, box in self.feature_checks.items()}
        ov, oe = self._tree_state('官方色库')
        fv, fe = self._tree_state('正式色库')
        try:
            self.auth.set_features(self.user.user_id, settings)
            self.auth.replace_scope_policy(
                self.user.user_id,
                official_view=ov,
                official_export=oe,
                official_grants=self._collect_branch('官方色库'),
                formal_all_view=fv,
                formal_all_export=fe,
                customer_grants=self._collect_branch('正式色库'),
                granted_by=self.current_user.username,
            )
            self.auth.log(self.current_user.username, 'SET_UNIFIED_PERMISSIONS', self.user.username,
                          'features=' + ','.join(k for k, v in settings.items() if v))
            self._refresh_effective_summary()
            self.saved.emit()
            return True
        except Exception as exc:
            QMessageBox.warning(self, '权限设置', str(exc))
            return False


class UnifiedPermissionDialog(QDialog):
    """Compatibility wrapper around the Hotfix58 editor."""
    def __init__(self, auth: AuthStore, user: AuthUser, resources: list[str], current_user: AuthUser, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f'{user.username} · {user.display_name} · 权限设置')
        self.resize(980, 720)
        root = QVBoxLayout(self)
        editor = PermissionEditor(auth, current_user, resources, self)
        editor.load_user(user)
        root.addWidget(editor)
        editor.saved.connect(self.accept)


class UserManagementDialog(QDialog):
    def __init__(self, auth: AuthStore, current_user: AuthUser, parent=None):
        super().__init__(parent)
        self.auth = auth
        self.current_user = current_user
        self.setWindowTitle('用户与权限')
        self.resize(1280, 800)
        self.setMinimumSize(1050, 680)
        self.setStyleSheet("""
            QDialog { background:#F4F7FB; color:#172033; }
            QFrame#userSidebar, QFrame#permCard, QFrame#permHeaderCard, QFrame#permSummaryCard { background:#FFFFFF; border:1px solid #E3E9F2; border-radius:14px; }
            QFrame#permHeaderCard { background:#FBFDFF; }
            QLabel#permUserTitle { font-size:20px; font-weight:700; color:#111827; }
            QLabel#permSectionTitle { font-size:15px; font-weight:700; color:#172033; }
            QLabel#permGroupLabel { font-size:12px; font-weight:600; color:#738198; padding-top:4px; }
            QLabel#permMuted { color:#7B879A; font-size:12px; }
            QLabel#permAvatar { background:#E8F1FF; color:#2F7FF7; border-radius:26px; font-size:22px; font-weight:700; }
            QLabel#permSummaryText { color:#35445D; font-size:12px; }
            QLineEdit#userSearch { background:#FFFFFF; border:1px solid #DDE5EF; border-radius:10px; padding:9px 12px; }
            QListWidget#userList { background:transparent; border:0; outline:0; }
            QListWidget#userList::item { background:transparent; border-radius:10px; padding:10px 12px; margin:2px 0; }
            QListWidget#userList::item:selected { background:#EAF3FF; color:#145EC7; }
            QTreeWidget#permissionTree { background:#FFFFFF; border:1px solid #E7ECF3; border-radius:10px; outline:0; }
            QTreeWidget#permissionTree::item { height:30px; }
            QHeaderView::section { background:#F7F9FC; color:#536178; border:0; border-bottom:1px solid #E7ECF3; padding:7px; font-weight:600; }
            QCheckBox#permCheck { spacing:10px; padding:5px 2px; }
            QPushButton { border:1px solid #D9E2EE; background:#FFFFFF; border-radius:9px; padding:8px 14px; }
            QPushButton:hover { background:#F5F8FC; }
            QPushButton#primaryButton { background:#247AF2; color:white; border:0; font-weight:600; }
            QPushButton#primaryButton:hover { background:#176BE0; }
            QPushButton#secondaryButton { background:#FFFFFF; }
            QFrame#permDivider { color:#E9EDF3; }
        """)

        root = QHBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(14)

        side = QFrame()
        side.setObjectName('userSidebar')
        side.setFixedWidth(300)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(14, 14, 14, 14)
        sl.setSpacing(10)
        side_title = QLabel('用户')
        side_title.setObjectName('permSectionTitle')
        sl.addWidget(side_title)
        self.search = QLineEdit()
        self.search.setObjectName('userSearch')
        self.search.setPlaceholderText('搜索姓名或账号…')
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter_users)
        sl.addWidget(self.search)
        self.list = QListWidget()
        self.list.setObjectName('userList')
        self.list.currentItemChanged.connect(self._selection_changed)
        sl.addWidget(self.list, 1)
        add = QPushButton('＋ 新建用户')
        add.setObjectName('primaryButton')
        add.clicked.connect(self.add_user)
        sl.addWidget(add)
        row = QHBoxLayout()
        reset = QPushButton('重置密码')
        reset.clicked.connect(self.reset_password)
        toggle = QPushButton('启用 / 停用')
        toggle.clicked.connect(self.toggle_enabled)
        row.addWidget(reset)
        row.addWidget(toggle)
        sl.addLayout(row)
        close = QPushButton('关闭')
        close.clicked.connect(self.accept)
        sl.addWidget(close)
        root.addWidget(side)

        self.editor = PermissionEditor(auth, current_user, self._resource_paths(), self)
        self.editor.saved.connect(self._permissions_saved)
        root.addWidget(self.editor, 1)
        self.reload()

    def _resource_paths(self):
        main = self.parent()
        store = getattr(main, 'store', None)
        resources = []
        if store is not None:
            try:
                resources.extend(list(store.customer_counts()))
            except Exception:
                pass
            try:
                resources.extend(list(store.list_customer_groups()))
            except Exception:
                pass
        return sorted(set(resources), key=str.casefold)

    def reload(self):
        selected = self._selected_id() if self.list.count() else None
        self.list.clear()
        selected_item = None
        for user in self.auth.list_users():
            role = '管理员' if user.is_admin else '使用者'
            state = '启用' if user.enabled else '停用'
            item = QListWidgetItem(f'{user.display_name or user.username}    {user.username}\n{role} · {state}')
            item.setData(Qt.UserRole, user.user_id)
            item.setSizeHint(QSize(250, 58))
            self.list.addItem(item)
            if user.user_id == selected:
                selected_item = item
        if selected_item:
            self.list.setCurrentItem(selected_item)
        elif self.list.count():
            self.list.setCurrentRow(0)
        self._filter_users(self.search.text() if hasattr(self, 'search') else '')
        self._selection_changed()

    def _filter_users(self, text=''):
        query = str(text or '').strip().casefold()
        for i in range(self.list.count()):
            item = self.list.item(i)
            item.setHidden(bool(query) and query not in item.text().casefold())

    def _selected_id(self):
        item = self.list.currentItem()
        return int(item.data(Qt.UserRole)) if item else None

    def _selected_user(self):
        uid = self._selected_id()
        return next((x for x in self.auth.list_users() if x.user_id == uid), None) if uid is not None else None

    def _selection_changed(self, *_args):
        self.editor.set_resources(self._resource_paths())
        self.editor.load_user(self._selected_user())

    def _permissions_saved(self):
        self.reload()
        QMessageBox.information(self, '权限设置', '权限已保存。重新登录后所有模块将按新的功能权限和数据访问范围执行。')

    def add_user(self):
        dlg = QDialog(self)
        dlg.setWindowTitle('新建账号')
        form = QFormLayout(dlg)
        user = QLineEdit()
        name = QLineEdit()
        password = QLineEdit()
        password.setEchoMode(QLineEdit.Password)
        role = QComboBox()
        role.addItem('使用者', 'operator')
        role.addItem('管理者', 'admin')
        form.addRow('账号', user)
        form.addRow('显示名称', name)
        form.addRow('密码', password)
        form.addRow('角色', role)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if dlg.exec() != QDialog.Accepted:
            return
        try:
            self.auth.create_user(user.text(), password.text(), role.currentData(), name.text())
            self.auth.log(self.current_user.username, 'CREATE_USER', user.text(), role.currentData())
            self.reload()
        except Exception as exc:
            QMessageBox.warning(self, '新建账号', str(exc))

    def reset_password(self):
        uid = self._selected_id()
        if uid is None:
            return
        from PySide6.QtWidgets import QInputDialog
        password, ok = QInputDialog.getText(self, '重置密码', '新密码：', QLineEdit.Password)
        if not ok:
            return
        try:
            self.auth.reset_password(uid, password)
            self.auth.log(self.current_user.username, 'RESET_PASSWORD', str(uid), '')
            QMessageBox.information(self, '重置密码', '密码已更新。')
        except Exception as exc:
            QMessageBox.warning(self, '重置密码', str(exc))

    def toggle_enabled(self):
        uid = self._selected_id()
        if uid is None:
            return
        if uid == self.current_user.user_id:
            QMessageBox.information(self, '用户与权限', '不能停用当前正在登录的账号。')
            return
        user = self._selected_user()
        if not user:
            return
        self.auth.set_enabled(uid, not user.enabled)
        self.auth.log(self.current_user.username, 'SET_USER_ENABLED', user.username, str(not user.enabled))
        self.reload()

    def edit_permissions(self):
        self.editor.load_user(self._selected_user())

def login_or_create_first_admin(auth: AuthStore, parent=None) -> AuthUser | None:
    if auth.user_count() == 0:
        dlg = FirstRunAdminDialog(parent)
        while True:
            if dlg.exec() != QDialog.Accepted:
                return None
            organization, username, password, password2, display = dlg.values()
            if not organization:
                QMessageBox.warning(dlg, "初始化组织", "请输入组织 / 公司名称。")
                continue
            if password != password2:
                QMessageBox.warning(dlg, "创建管理者", "两次输入的密码不一致。")
                continue
            try:
                auth.set_organization_name(organization)
                user=auth.create_user(username, password, "admin", display)
                recovery_key=auth.generate_recovery_key(changed_by=user.username)
                auth.log(user.username,'INITIALIZE_ORGANIZATION',organization,'first_admin')
                QMessageBox.information(dlg,'组织恢复密钥',
                    '请将下面的组织恢复密钥保存在安全位置。\n它用于所有管理者都无法登录时重置管理者密码；开发者不会保存该密钥，也没有隐藏万能管理员。\n\n'+recovery_key)
                return user
            except Exception as exc:
                QMessageBox.warning(dlg, "创建管理者", str(exc))
    dlg = LoginDialog(auth, parent)
    return dlg.result_user if dlg.exec() == QDialog.Accepted else None
