"""RBAC 2.0 data-governance vocabulary.

DG-2 activates customer-level visibility/export enforcement while preserving a
compatibility mode for pre-DG accounts until an administrator saves a policy.
"""
from __future__ import annotations

from dataclasses import dataclass


OFFICIAL_ROOT = "官方色库"


def normalize_scope_key(value: str) -> str:
    return "/".join(p.strip() for p in str(value or "").replace("\\", "/").split("/") if p.strip())


def data_class_for_customer(customer: str) -> str:
    value = normalize_scope_key(customer)
    if value == OFFICIAL_ROOT or value.startswith(OFFICIAL_ROOT + "/"):
        return "official"
    return "formal"


@dataclass(frozen=True)
class DataScopeGrant:
    user_id: int
    scope_type: str
    scope_key: str
    can_view: bool = True
    can_export: bool = False

    def matches_customer(self, customer: str) -> bool:
        customer = normalize_scope_key(customer)
        if self.scope_type == "official":
            return data_class_for_customer(customer) == "official"
        if self.scope_type == "formal":
            return data_class_for_customer(customer) == "formal"
        if self.scope_type == "customer":
            base = normalize_scope_key(self.scope_key)
            return bool(base) and (customer == base or customer.startswith(base + "/"))
        return False
