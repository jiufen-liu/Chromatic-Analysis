"""Bind the authenticated user's library scope before any colour-data reads."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def configure_library_access(store, auth_store, user) -> dict:
    try:
        policy = dict(auth_store.data_scope_policy(user))
        # Unrestricted mode is an administrator privilege, not a fallback for
        # missing operator metadata. Legacy operator mode still materialises ACLs.
        if policy.get('unrestricted') and user.is_admin:
            store.set_exact_access_paths(None)
        else:
            paths = list(store.customer_counts())
            allowed = [p for p in paths if auth_store.can_view_customer(user, p)]
            store.set_exact_access_paths(allowed)
            policy['_exact_visible_paths'] = allowed
        return policy
    except Exception:
        logger.exception('Cannot load library access policy; using role-safe fallback')
        admin = bool(user.is_admin)
        store.set_exact_access_paths(None if admin else [])
        return {
            'configured': False, 'unrestricted': admin,
            'official_view': admin, 'formal_all_view': admin,
            'officials': {}, 'customers': {},
            '_exact_visible_paths': [], 'load_failed': True,
        }
