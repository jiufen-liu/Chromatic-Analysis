from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QCoreApplication

from qtx_app.auth_store import AuthStore, FEATURES
from qtx_app.data_governance import data_class_for_customer
from qtx_app.library_store import LibraryStore


def yes(value):
    return "YES" if value else "NO"


def main():
    app = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
    app.setApplicationName("QTX 色彩分析")

    auth = AuthStore()
    store = LibraryStore()
    users = auth.list_users()
    files = store.list_files()
    counts = store.customer_counts()
    cards = store.list_color_cards()
    workbenches = store.list_workbenches()

    classes = Counter()
    for customer, count in counts.items():
        classes[data_class_for_customer(customer)] += int(count)

    print("Chromatic Analysis · Data Governance / RBAC 2.0 · DG-1 Audit")
    print("=" * 72)
    print(f"Auth DB:       {auth.path}")
    print(f"Library DB:    {store.path}")
    print(f"Customer QTX:  {store.customer_data_root}")
    print()
    print(f"Users:         {len(users)}")
    print(f"Library QTX:   {len(files)}")
    print(f"Official samples: {classes['official']}")
    print(f"Formal samples:   {classes['formal']}")
    print(f"Color cards:   {len(cards)}  (current schema: global/shared)")
    print(f"Workbenches:   {len(workbenches)}  (current schema: global/shared)")
    print()
    print("Users / feature permissions")
    for user in users:
        allowed = auth.allowed_features(user)
        feature_text = ", ".join(k for k in FEATURES if allowed.get(k)) or "(none)"
        grants = auth.list_scope_grants(user.user_id)
        print(f"- {user.username} [{user.role}] enabled={yes(user.enabled)}")
        print(f"  features: {feature_text}")
        print(f"  data-scope grants: {len(grants)} (DG-1 only; NOT enforced until DG-2)")
    print()
    print("DG-1 findings")
    print("- Current library visibility is feature-level only; there is no customer ACL in active queries.")
    print("- Formal/official libraries share the same library SQLite and are classified by customer path.")
    print("- Customer QTX mirrors live under Documents and can be browsed outside the application.")
    print("- Color cards and workbenches do not currently carry owner_user_id; they are shared records.")
    print("- Personal .chromatic workfiles can be saved to arbitrary user-selected paths.")
    print("- Navigation/favourites are already user-scoped through QSettings and can be retained.")
    print("- Destructive formal-library operations are protected by administrator checks.")
    print("- Export is a feature permission, but clipboard/data-exfiltration controls are not yet unified.")
    print()
    print("DG-2 target: enforce official read-only + formal customer grants + per-user private workspace.")

    report = {
        "auth_db": str(auth.path),
        "library_db": str(store.path),
        "customer_qtx_root": str(store.customer_data_root),
        "users": [
            {
                "user_id": u.user_id,
                "username": u.username,
                "role": u.role,
                "enabled": u.enabled,
                "features": auth.allowed_features(u),
                "scope_grants": [dict(r) for r in auth.list_scope_grants(u.user_id)],
            }
            for u in users
        ],
        "library_qtx_count": len(files),
        "official_sample_count": classes["official"],
        "formal_sample_count": classes["formal"],
        "color_card_count": len(cards),
        "workbench_count": len(workbenches),
    }
    out = ROOT / "data_governance_audit.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nJSON: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
