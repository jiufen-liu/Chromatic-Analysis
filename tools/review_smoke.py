import os, sys, time, json, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from qtx_app.storage_v2 import _write_config
app=QApplication([]); app.setApplicationName('ChromaticReviewSmoke')
runtime = Path(os.environ.get('CHROMATIC_SMOKE_DIR') or tempfile.mkdtemp(prefix='chromatic-smoke-'))
runtime.mkdir(parents=True, exist_ok=True)
_write_config({'system_root':str(runtime/'ui-system'),'business_root':str(runtime/'ui-business')})
from qtx_app.auth_store import AuthStore, AuthUser
from qtx_app.main_window import MainWindow
from qtx_core.qtx_parser import parse_qtx_file
root=str(ROOT)
auth=AuthStore()
window=MainWindow(current_user=AuthUser(0,'review','Review','admin'),auth_store=auth)
window.resize(1366,768);window.show()
def validate():
 try:
  samples=parse_qtx_file(Path(root)/'tests/data/Col.9 FH0092.qtx')
  window.store.save_file(str(Path(root)/'tests/data/Col.9 FH0092.qtx'),'审查测试',samples)
  rows=window.store.load_samples(str(Path(root)/'tests/data/Col.9 FH0092.qtx'))
  assert len(rows)==3
  window._switch_library_scope_and_open('正式色库','审查测试')
  app.processEvents()
  assert 'D65 / 10°' in window.library_condition_label.text()
  window.grab().save(str(runtime/'ui-1366.png'))
  window.light.setCurrentText('U30')
  window.conditions_changed()
  app.processEvents()
  assert '兼容光谱' in window.library_condition_label.text()
  assert not window._preview_condition_failures, 'Complete reference spectra must not fall back'
  window.grab().save(str(runtime/'ui-commercial-1366.png'))
  window.light.setCurrentText('D65')
  window.conditions_changed()
  app.processEvents()
  for name,path in [('colour',window.store.path),('security',auth.path),('audit',auth.audit_store.path)]:
   import sqlite3
   with sqlite3.connect(path) as db: assert db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
  print('PASS: main window, QTX parse, SQLite save/load, scope switch, condition banner, commercial SPD warning, three database integrity checks',flush=True)
  window.close();app.exit(0)
 except Exception:
  import traceback;traceback.print_exc();app.exit(1)
QTimer.singleShot(1500,validate)
sys.exit(app.exec())
