"""Run with PySide6 and pytest, e.g. QT_QPA_PLATFORM=offscreen pytest -q tests/test_ui_selection_scope.py."""
from types import SimpleNamespace
from pathlib import Path
from dataclasses import asdict
from qtx_core.models import Sample

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractItemView, QApplication, QLabel, QListWidget, QTabBar

from qtx_app.main_window import ColorCardPlanWindow, MainWindow


def key(source, index):
    return f"{Path(source).resolve()}|{index}"


def resolve_samples(keys, **kwargs):
    return [Sample(k.rpartition('|')[2], k.rpartition('|')[2], 'STD',
                   (20,21,22),(50,1,2),(),source_file=k.rpartition('|')[0]) for k in keys]


def app():
    return QApplication.instance() or QApplication([])


def test_palette_select_all_with_persistent_selection_and_ctrl_toggle():
    app()
    main=SimpleNamespace(
        _all_library_samples=lambda: [],
        _samples_by_keys_on_demand=resolve_samples,
        _serialize_sample=asdict,
        sample_lab=lambda sm: sm.lab_d65_10,
        store=SimpleNamespace(list_color_cards=lambda: [], load_samples_by_sample_ids=lambda ids: []),
        statusBar=lambda: SimpleNamespace(showMessage=lambda *args: None),
    )
    card={'card_id':'test','name':'79 色卡方案','layout':[{'sample_key':key('file',i)} for i in range(79)],
          'settings':{'grid_cols':10,'auto_grid':True}}
    plan = ColorCardPlanWindow(main,card)
    plan.show()
    QApplication.processEvents()
    QTest.keyClick(plan.grid, Qt.Key_A, Qt.ControlModifier)
    assert len(plan.grid.selectedItems()) == 80
    assert sum(bool(item.data(Qt.UserRole)) for item in plan.grid.selectedItems()) == 79
    assert len(plan.grid.selectionModel().selectedIndexes()) == 80
    assert plan.grid.dragDropMode()==QAbstractItemView.DragDrop
    assert sum(not bool(item.data(Qt.UserRole)) for item in plan.grid.selectedItems()) == 1
    QTest.keyClick(plan.grid, Qt.Key_C, Qt.ControlModifier)
    assert len(plan._clipboard_keys)==79
    assert len(QApplication.clipboard().text().splitlines())==79
    item = plan.grid.item(0)
    pos = plan.grid.visualRect(plan.grid.index_for_linear(0)).center()
    QTest.mouseClick(plan.grid.viewport(), Qt.LeftButton, Qt.ControlModifier, pos=pos)
    assert len(plan.grid.selectedItems()) == 79
    assert sum(bool(item.data(Qt.UserRole)) for item in plan.grid.selectedItems()) == 78
    assert len(plan.grid.selectionModel().selectedIndexes()) == 79
    plan.close()


def test_palette_shortcuts_target_only_the_focused_scheme():
    app()
    main=SimpleNamespace(_all_library_samples=lambda: [],
        _samples_by_keys_on_demand=resolve_samples,
        _serialize_sample=asdict,
        sample_lab=lambda sm: sm.lab_d65_10,
        store=SimpleNamespace(list_color_cards=lambda: [], load_samples_by_sample_ids=lambda ids: []),
                         statusBar=lambda: SimpleNamespace(showMessage=lambda *args: None))
    one=ColorCardPlanWindow(main,{'card_id':'one','name':'方案一',
                                   'layout':[{'sample_key':key('one',0)},{'sample_key':key('one',1)}],
                                   'settings':{'grid_cols':2}})
    two=ColorCardPlanWindow(main,{'card_id':'two','name':'方案二',
                                   'layout':[{'sample_key':key('two',0)}],
                                   'settings':{'grid_cols':2}})
    one.show(); QApplication.processEvents()
    QTest.keyClick(one.grid,Qt.Key_A,Qt.ControlModifier)
    assert len(one.grid.selectedItems())==2
    QTest.keyClick(one.grid,Qt.Key_C,Qt.ControlModifier)
    assert len(one._clipboard_keys)==2 and not two._clipboard_keys
    two.show(); QApplication.processEvents()
    QTest.keyClick(two.grid,Qt.Key_A,Qt.ControlModifier)
    assert len(two.grid.selectedItems())==2
    assert sum(bool(item.data(Qt.UserRole)) for item in two.grid.selectedItems())==1
    two.close(); one.close()


def test_switch_library_scope_removes_old_qtabbar_entries():
    app()
    tabs = QTabBar()
    tabs.addTab('正式客户')
    fake = SimpleNamespace(
        library_scope='正式色库', library_customer_tabs=tabs,
        library_group_title=QLabel(), library_group_hint=QLabel(),
        open_library_customers=['正式客户'], minimized_library_customers=[],
        active_library_customer='正式客户', file_list=QListWidget(),
        library_selected_keys={'formal|1'}, hidden_cards={'formal|2'},
        library_page=1, _tiles_keys=['formal|1'], tool_subwindows={},
    )
    def open_customer(name):
        fake.open_library_customers.append(name)
        fake.library_customer_tabs.addTab(name)
        fake.active_library_customer=name
    fake.open_library_customer=open_customer
    fake.refresh_library_customer_tree=lambda: None
    MainWindow._library_scope_changed(fake,'官方色库')
    assert fake.library_scope=='官方色库'
    assert tabs.count()==0
    assert not fake.library_selected_keys and not fake.hidden_cards
    assert fake.active_library_customer is None
    assert fake.library_has_explicit_view is False


def test_formal_and_official_sql_scopes_do_not_mix(tmp_path):
    from qtx_app.library_store import LibraryStore
    store=LibraryStore.__new__(LibraryStore)
    store.path=tmp_path/'library.sqlite3'
    store.set_access_scope(None)
    with store.connect() as db:
        db.execute('CREATE TABLE saved_qtx(path TEXT,customer TEXT,position INTEGER,mirror_path TEXT)')
        db.execute('CREATE TABLE saved_samples(qtx_path TEXT,payload TEXT)')
        db.executemany('INSERT INTO saved_qtx VALUES(?,?,?,?)',[
            ('/tmp/a.qtx','客户A',0,''),('/tmp/p.qtx','官方色库/Pantone',1,''),
            ('/tmp/r.qtx','官方色库/RAL',2,''),
        ])
    formal=[row.customer for row,_ in store.library_contents(scope='正式色库')]
    official=[row.customer for row,_ in store.library_contents(scope='官方色库')]
    assert formal==['客户A']
    assert official==['官方色库/Pantone','官方色库/RAL']
