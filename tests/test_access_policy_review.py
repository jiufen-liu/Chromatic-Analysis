from types import SimpleNamespace

from qtx_app.access_policy import configure_library_access


class Store:
    def set_exact_access_paths(self, paths): self.paths = paths
    def customer_counts(self): return {'客户A': 2, '客户B': 1}


def broken(*args): raise RuntimeError('permission database unavailable')


def test_operator_permission_failure_denies_all_library_paths():
    store=Store()
    policy=configure_library_access(store,SimpleNamespace(data_scope_policy=broken),SimpleNamespace(is_admin=False))
    assert store.paths == []
    assert policy['load_failed'] and not policy['unrestricted']


def test_admin_permission_failure_retains_repair_access():
    store=Store()
    policy=configure_library_access(store,SimpleNamespace(data_scope_policy=broken),SimpleNamespace(is_admin=True))
    assert store.paths is None and policy['load_failed']


def test_operator_allowed_path_list_respects_child_denials():
    store=Store()
    auth=SimpleNamespace(data_scope_policy=lambda user: {'unrestricted':False},
                         can_view_customer=lambda user,path: path=='客户A')
    policy=configure_library_access(store,auth,SimpleNamespace(is_admin=False))
    assert store.paths == ['客户A'] and policy['_exact_visible_paths']==['客户A']


def test_operator_cannot_receive_admin_unrestricted_fallback():
    store=Store()
    auth=SimpleNamespace(data_scope_policy=lambda user: {'unrestricted':True},can_view_customer=lambda user,path:False)
    configure_library_access(store,auth,SimpleNamespace(is_admin=False))
    assert store.paths == []
