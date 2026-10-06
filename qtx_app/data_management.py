from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QAbstractItemView, QInputDialog,
    QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from .audit_export import export_audit_workbook
from .data_protection import (
    backup_root, managed_data_root, create_database_backup, restore_database_backup,
    create_color_data_backup, create_security_backup, archive_audit_database,
    restore_color_data_backup, restore_security_backup,
    installation_info, set_installation_label, export_portable_data_package,
    import_portable_data_package, managed_workfile_count, transfer_managed_workfiles,
    rebase_library_mirror_paths,
)
from .storage_v2 import (
    business_data_root, system_data_root, formal_library_root, color_card_root,
    personal_workspace_root, business_backup_root, security_database_path,
    color_database_path, audit_database_path, migrate_business_data_root,
    storage_health_report, rebuild_readable_snapshots, cleanup_readable_snapshots_for_user,
    full_system_backup_root, security_backup_root, audit_backup_root, legacy_archive_root,
)


class DataManagementDialog(QDialog):
    """DG-3.2 local administration console.

    The dialog is intentionally self-contained: Hotfix62 changes only this
    management surface and the audit query/export helpers.  The studio/main
    window layout is not modified.
    """

    def __init__(self, auth_store, library_store, current_user, parent=None):
        super().__init__(parent)
        self.auth_store = auth_store
        self.library_store = library_store
        self.current_user = current_user
        self._audit_rows = []

        self.setWindowTitle('数据管理中心 · DG4 色彩数据 / 用户权限 / 备份 / 审计')
        self.setMinimumSize(780, 560)
        screen = QApplication.primaryScreen()
        if screen:
            area = screen.availableGeometry()
            self.resize(min(1080, max(820, int(area.width() * 0.82))),
                        min(820, max(600, int(area.height() * 0.84))))
        else:
            self.resize(1000, 720)

        self.setStyleSheet('''
            QDialog { background: #F5F7FA; }
            QGroupBox {
                background: #FFFFFF; border: 1px solid #E2E8F0;
                border-radius: 10px; margin-top: 13px; padding-top: 10px;
                font-weight: 700; color: #172033;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 6px; }
            QPushButton {
                min-height: 30px; padding: 0 12px; border-radius: 7px;
                border: 1px solid #D5DCE6; background: #FFFFFF; color: #263449;
            }
            QPushButton:hover { background: #F4F7FB; border-color: #B9C6D8; }
            QPushButton#primaryButton { background: #2F7CF6; color: white; border: none; font-weight: 600; }
            QPushButton#primaryButton:hover { background: #246CE0; }
            QLineEdit, QComboBox {
                min-height: 30px; border: 1px solid #D9E0EA; border-radius: 7px;
                background: white; padding: 0 8px;
            }
            QTableWidget {
                background: white; border: 1px solid #E4E9F0; border-radius: 7px;
                gridline-color: #EDF1F5; selection-background-color: #EAF2FF;
                selection-color: #172033;
            }
            QHeaderView::section {
                background: #F7F9FC; border: none; border-bottom: 1px solid #E3E8EF;
                padding: 7px; font-weight: 600; color: #445167;
            }
        ''')

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll, 1)
        content = QWidget()
        scroll.setWidget(content)
        root = QVBoxLayout(content)
        root.setContentsMargins(8, 4, 8, 8)
        root.setSpacing(10)

        title = QLabel('数据管理中心 · DG4')
        title.setStyleSheet('font-size:20px;font-weight:750;color:#172033;')
        root.addWidget(title)
        tip = QLabel('色彩业务数据、用户身份与权限、个人工作区、审计记录分域管理。管理员管理系统与数据归属，默认不直接读取其他用户的私人工作内容。')
        tip.setWordWrap(True)
        tip.setStyleSheet('color:#667085;margin-bottom:2px;')
        root.addWidget(tip)

        domains = QGroupBox('DG4 数据域 · 业务数据与用户安全分离')
        dl = QVBoxLayout(domains); dl.setSpacing(8)
        self.domain_paths = QLabel(); self.domain_paths.setWordWrap(True); self.domain_paths.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.domain_paths.setStyleSheet('color:#475467;')
        dl.addWidget(self.domain_paths)
        drow1 = QHBoxLayout()
        for text_, path_fn in ((
            '打开正式色库目录', formal_library_root), ('打开色卡编排目录', color_card_root),
            ('打开个人工作区', personal_workspace_root), ('打开系统数据目录', system_data_root),
            ('打开旧版迁移源', legacy_archive_root),
        ):
            btn = QPushButton(text_); btn.clicked.connect(lambda _=False, fn=path_fn: self._open(fn())); drow1.addWidget(btn)
        drow1.addStretch(1); dl.addLayout(drow1)
        drow2 = QHBoxLayout()
        migrate_btn = QPushButton('迁移业务数据位置…'); migrate_btn.clicked.connect(self._migrate_business_root); drow2.addWidget(migrate_btn)
        rebuild_btn = QPushButton('重建可读镜像'); rebuild_btn.clicked.connect(self._rebuild_readable_mirrors); drow2.addWidget(rebuild_btn)
        health_btn = QPushButton('检查数据完整性'); health_btn.setObjectName('primaryButton'); health_btn.clicked.connect(self._check_storage_health); drow2.addWidget(health_btn)
        drow2.addStretch(1); dl.addLayout(drow2)
        root.addWidget(domains)

        top_grid = QGridLayout()
        top_grid.setContentsMargins(0, 0, 0, 0)
        top_grid.setHorizontalSpacing(10)
        top_grid.setVerticalSpacing(10)
        top_grid.setColumnStretch(0, 3)
        top_grid.setColumnStretch(1, 2)
        root.addLayout(top_grid)

        identity = QGroupBox('组织与安装实例')
        il = QVBoxLayout(identity)
        il.setSpacing(8)
        self.org_label = QLabel()
        self.install_label = QLabel()
        self.install_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        il.addWidget(self.org_label)
        il.addWidget(self.install_label)
        ir = QHBoxLayout()
        rename_org = QPushButton('修改组织名称…'); rename_org.clicked.connect(self._rename_org); ir.addWidget(rename_org)
        rename_install = QPushButton('修改本机实例名称…'); rename_install.clicked.connect(self._rename_install); ir.addWidget(rename_install)
        recovery = QPushButton('重新生成恢复密钥…'); recovery.clicked.connect(self._rotate_recovery_key); ir.addWidget(recovery)
        ir.addStretch(1)
        il.addLayout(ir)
        top_grid.addWidget(identity, 0, 0)

        portable = QGroupBox('电脑迁移 / 可携数据包')
        pl = QVBoxLayout(portable)
        pl.setSpacing(8)
        ptip = QLabel('用于开发电脑或客户换机。数据包包含用户与权限、色彩数据库、审计记录、正式色库镜像和个人工作数据；本机 Installation ID 不会被覆盖。\n注意：当前数据包未加密，请按敏感业务数据保管。')
        ptip.setWordWrap(True); ptip.setStyleSheet('color:#667085;'); pl.addWidget(ptip)
        pr = QHBoxLayout()
        export = QPushButton('导出数据包…'); export.setObjectName('primaryButton'); export.clicked.connect(self._export_package); pr.addWidget(export)
        imp = QPushButton('导入数据包…'); imp.clicked.connect(self._import_package); pr.addWidget(imp)
        pr.addStretch(1); pl.addLayout(pr)
        top_grid.addWidget(portable, 0, 1)

        protection = QGroupBox('分域备份与恢复')
        bl = QVBoxLayout(protection)
        bl.setSpacing(8)
        self.path_label = QLabel(); self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse); self.path_label.setWordWrap(True); bl.addWidget(self.path_label)
        backup_row = QHBoxLayout()
        backup = QPushButton('完整系统备份'); backup.setObjectName('primaryButton'); backup.clicked.connect(self._backup); backup_row.addWidget(backup)
        color_backup = QPushButton('仅备份色彩数据'); color_backup.clicked.connect(self._backup_color); backup_row.addWidget(color_backup)
        security_backup = QPushButton('仅备份用户与权限'); security_backup.clicked.connect(self._backup_security); backup_row.addWidget(security_backup)
        audit_archive = QPushButton('归档审计'); audit_archive.clicked.connect(self._archive_audit); backup_row.addWidget(audit_archive)
        backup_row.addStretch(1); bl.addLayout(backup_row)
        restore_row = QHBoxLayout()
        open_data = QPushButton('打开业务数据'); open_data.clicked.connect(lambda:self._open(business_data_root())); restore_row.addWidget(open_data)
        open_backups = QPushButton('打开完整备份'); open_backups.clicked.connect(lambda:self._open(full_system_backup_root())); restore_row.addWidget(open_backups)
        restore = QPushButton('完整恢复…'); restore.clicked.connect(self._restore); restore_row.addWidget(restore)
        restore_color = QPushButton('恢复色彩数据…'); restore_color.clicked.connect(self._restore_color); restore_row.addWidget(restore_color)
        restore_security = QPushButton('恢复用户与权限…'); restore_security.clicked.connect(self._restore_security); restore_row.addWidget(restore_security)
        restore_row.addStretch(1); bl.addLayout(restore_row)
        root.addWidget(protection)

        ownership = QGroupBox('个人数据归属（只显示数量，不读取内容）')
        ol = QVBoxLayout(ownership)
        ol.setSpacing(7)
        otip = QLabel('用户离职或工作交接时，可由管理员整体转移“归属”。转移操作不会打开比色、色卡或个人文件内容。')
        otip.setWordWrap(True); otip.setStyleSheet('color:#667085;'); ol.addWidget(otip)
        self.owner_table = QTableWidget(0, 4)
        self.owner_table.setHorizontalHeaderLabels(['用户','比色工作台','色卡方案','受管个人文件'])
        self.owner_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for c in (1,2,3): self.owner_table.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.owner_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.owner_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.owner_table.setMaximumHeight(180)
        ol.addWidget(self.owner_table)
        tr = QHBoxLayout(); tr.addWidget(QLabel('从'))
        self.source_user = QComboBox(); tr.addWidget(self.source_user, 1); tr.addWidget(QLabel('转移给'))
        self.target_user = QComboBox(); tr.addWidget(self.target_user, 1)
        transfer = QPushButton('转移个人数据归属…'); transfer.clicked.connect(self._transfer); tr.addWidget(transfer)
        ol.addLayout(tr)
        root.addWidget(ownership)

        audit = QGroupBox('审计记录')
        al = QVBoxLayout(audit)
        al.setSpacing(7)
        audit_tip = QLabel('筛选当前组织的本地审计记录；导出 Excel 只导出当前筛选结果，且导出行为本身会继续写入审计日志。')
        audit_tip.setWordWrap(True); audit_tip.setStyleSheet('color:#667085;'); al.addWidget(audit_tip)

        filters = QHBoxLayout()
        self.audit_period = QComboBox()
        self.audit_period.addItem('全部时间', 0)
        self.audit_period.addItem('今天', 1)
        self.audit_period.addItem('最近 7 天', 7)
        self.audit_period.addItem('最近 30 天', 30)
        self.audit_period.addItem('最近 90 天', 90)
        filters.addWidget(self.audit_period)
        self.audit_user = QComboBox(); self.audit_user.addItem('全部用户', ''); filters.addWidget(self.audit_user)
        self.audit_action = QComboBox(); self.audit_action.addItem('全部操作', ''); filters.addWidget(self.audit_action)
        self.audit_keyword = QLineEdit(); self.audit_keyword.setPlaceholderText('搜索目标或详情…'); filters.addWidget(self.audit_keyword, 1)
        reset = QPushButton('清除筛选'); reset.clicked.connect(self._reset_audit_filters); filters.addWidget(reset)
        refresh = QPushButton('刷新'); refresh.clicked.connect(self._refresh_audit); filters.addWidget(refresh)
        export_audit = QPushButton('导出 Excel…'); export_audit.setObjectName('primaryButton'); export_audit.clicked.connect(self._export_audit_excel); filters.addWidget(export_audit)
        al.addLayout(filters)

        self.audit_count = QLabel('')
        self.audit_count.setStyleSheet('color:#667085;')
        al.addWidget(self.audit_count)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(['时间','用户','操作','目标','详情'])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setMinimumHeight(210)
        al.addWidget(self.table, 1)
        root.addWidget(audit, 1)

        close = QPushButton('关闭'); close.clicked.connect(self.accept)
        bottom = QHBoxLayout(); bottom.addStretch(1); bottom.addWidget(close); outer.addLayout(bottom)

        self._audit_search_timer = QTimer(self)
        self._audit_search_timer.setSingleShot(True)
        self._audit_search_timer.setInterval(220)
        self._audit_search_timer.timeout.connect(self._refresh_audit)
        self.audit_keyword.textChanged.connect(lambda _text: self._audit_search_timer.start())
        self.audit_period.currentIndexChanged.connect(lambda _index: self._refresh_audit())
        self.audit_user.currentIndexChanged.connect(lambda _index: self._refresh_audit())
        self.audit_action.currentIndexChanged.connect(lambda _index: self._refresh_audit())

        self.reload()

    def reload(self):
        info = installation_info()
        self.org_label.setText(f'组织：{self.auth_store.organization_name()}')
        self.install_label.setText(f'本机实例：{info.get("label","")}\nInstallation ID：{info.get("installation_id","")}')
        self.path_label.setText(
            f'业务数据备份：{business_backup_root()}\n'
            f'完整系统备份：{full_system_backup_root()}\n'
            f'用户权限备份：{security_backup_root()}\n'
            f'审计归档：{audit_backup_root()}'
        )
        self.domain_paths.setText(
            f'业务数据（用户可查找）：{business_data_root()}\n'
            f'系统数据（程序管理）：{system_data_root()}\n'
            f'色彩数据库：{color_database_path()}\n'
            f'用户与权限数据库：{security_database_path()}\n'
            f'审计数据库：{audit_database_path()}\n'
            f'旧版迁移源：{legacy_archive_root()}'
        )

        users = self.auth_store.list_users()
        summary = {int(x['owner_user_id']):x for x in self.library_store.private_data_summary()}
        rows = []
        for u in users:
            item = summary.get(u.user_id,{})
            rows.append((u,int(item.get('workbenches',0)),int(item.get('color_cards',0)),managed_workfile_count(u.user_id,u.username)))
        self.owner_table.setRowCount(len(rows))
        for r,(u,w,c,f) in enumerate(rows):
            vals = [f'{u.display_name or u.username}  ({u.username})',w,c,f]
            for col,val in enumerate(vals): self.owner_table.setItem(r,col,QTableWidgetItem(str(val)))
        for combo in (self.source_user,self.target_user):
            current = combo.currentData(); combo.blockSignals(True); combo.clear()
            for u in users: combo.addItem(f'{u.display_name or u.username} ({u.username})',u.user_id)
            idx = combo.findData(current)
            if idx >= 0: combo.setCurrentIndex(idx)
            combo.blockSignals(False)
        self._refresh_audit_filter_values()
        self._refresh_audit()

    def _refresh_audit_filter_values(self):
        current_user = self.audit_user.currentData() if self.audit_user.count() else ''
        current_action = self.audit_action.currentData() if self.audit_action.count() else ''
        users, actions = self.auth_store.audit_filter_values()
        self.audit_user.blockSignals(True)
        self.audit_action.blockSignals(True)
        self.audit_user.clear(); self.audit_user.addItem('全部用户','')
        for value in users: self.audit_user.addItem(value,value)
        self.audit_action.clear(); self.audit_action.addItem('全部操作','')
        for value in actions: self.audit_action.addItem(value,value)
        idx = self.audit_user.findData(current_user); self.audit_user.setCurrentIndex(idx if idx >= 0 else 0)
        idx = self.audit_action.findData(current_action); self.audit_action.setCurrentIndex(idx if idx >= 0 else 0)
        self.audit_user.blockSignals(False)
        self.audit_action.blockSignals(False)

    def _audit_since(self):
        days = int(self.audit_period.currentData() or 0)
        if days <= 0:
            return None
        now = time.time()
        if days == 1:
            t = time.localtime(now)
            return time.mktime((t.tm_year,t.tm_mon,t.tm_mday,0,0,0,t.tm_wday,t.tm_yday,t.tm_isdst))
        return now - days * 86400

    def _audit_query_kwargs(self, *, export=False):
        return dict(
            since=self._audit_since(),
            username=str(self.audit_user.currentData() or ''),
            action=str(self.audit_action.currentData() or ''),
            keyword=self.audit_keyword.text().strip(),
            limit=None if export else 1001,
        )

    def _refresh_audit(self):
        try:
            rows = self.auth_store.query_audit(**self._audit_query_kwargs(export=False))
        except Exception as exc:
            self.audit_count.setText(f'读取审计记录失败：{exc}')
            return
        truncated = len(rows) > 1000
        self._audit_rows = list(rows[:1000])
        self.table.setRowCount(len(self._audit_rows))
        for r,row in enumerate(self._audit_rows):
            vals = [
                time.strftime('%Y-%m-%d %H:%M:%S',time.localtime(float(row['created_at']))),
                row['username'],row['action'],row['target'],row['detail'],
            ]
            for c,v in enumerate(vals):
                item = QTableWidgetItem(str(v or ''))
                item.setToolTip(str(v or ''))
                self.table.setItem(r,c,item)
        suffix = '（表格仅显示前 1000 条；导出 Excel 可导出全部筛选结果）' if truncated else ''
        self.audit_count.setText(f'当前筛选：{len(self._audit_rows)} 条{suffix}')

    def _reset_audit_filters(self):
        self.audit_period.setCurrentIndex(0)
        self.audit_user.setCurrentIndex(0)
        self.audit_action.setCurrentIndex(0)
        self.audit_keyword.clear()
        self._refresh_audit()

    def _audit_filter_description(self):
        parts = [self.audit_period.currentText()]
        if self.audit_user.currentData(): parts.append(f'用户={self.audit_user.currentText()}')
        if self.audit_action.currentData(): parts.append(f'操作={self.audit_action.currentText()}')
        if self.audit_keyword.text().strip(): parts.append(f'关键词={self.audit_keyword.text().strip()}')
        return '；'.join(parts)

    def _export_audit_excel(self):
        if not getattr(self.current_user,'is_admin',False):
            QMessageBox.information(self,'权限限制','只有组织管理员可以导出审计记录。')
            return
        try:
            rows = self.auth_store.query_audit(**self._audit_query_kwargs(export=True))
        except Exception as exc:
            QMessageBox.warning(self,'导出审计记录',f'读取审计记录失败：{exc}')
            return
        if not rows:
            QMessageBox.information(self,'导出审计记录','当前筛选条件下没有可导出的审计记录。')
            return
        suggested = f'Audit_{time.strftime("%Y%m%d_%H%M%S")}.xlsx'
        path,_ = QFileDialog.getSaveFileName(self,'导出审计记录 Excel',suggested,'Excel Workbook (*.xlsx)')
        if not path: return
        info = installation_info()
        try:
            out = export_audit_workbook(
                path, rows,
                organization_name=self.auth_store.organization_name(),
                installation_id=str(info.get('installation_id','')),
                installation_label=str(info.get('label','')),
                exported_by=self.current_user.username,
                filter_description=self._audit_filter_description(),
            )
            self.auth_store.log(
                self.current_user.username,'EXPORT_AUDIT_EXCEL',str(out),
                f'records={len(rows)}; filters={self._audit_filter_description()}'
            )
            self._refresh_audit_filter_values(); self._refresh_audit()
            QMessageBox.information(self,'导出完成',f'已导出 {len(rows)} 条审计记录：\n{out}')
        except Exception as exc:
            QMessageBox.warning(self,'导出失败',str(exc))

    def _rename_org(self):
        name,ok=QInputDialog.getText(self,'组织名称','组织 / 公司名称：',text=self.auth_store.organization_name())
        if not ok:return
        try:
            self.auth_store.set_organization_name(name,changed_by=self.current_user.username); self.reload()
        except Exception as exc: QMessageBox.warning(self,'组织名称',str(exc))

    def _rename_install(self):
        info=installation_info(); name,ok=QInputDialog.getText(self,'本机实例名称','实例名称：',text=str(info.get('label','')))
        if not ok:return
        try:
            set_installation_label(name); self.auth_store.log(self.current_user.username,'SET_INSTALLATION_LABEL',name,''); self.reload()
        except Exception as exc: QMessageBox.warning(self,'实例名称',str(exc))

    def _rotate_recovery_key(self):
        reply=QMessageBox.warning(self,'重新生成恢复密钥','生成新密钥后，旧的组织恢复密钥将立即失效。\n请确认当前管理者会把新密钥保存在安全位置。\n\n继续吗？',QMessageBox.Yes|QMessageBox.Cancel,QMessageBox.Cancel)
        if reply!=QMessageBox.Yes:return
        try:
            key=self.auth_store.generate_recovery_key(changed_by=self.current_user.username)
            QMessageBox.information(self,'新的组织恢复密钥','请安全保存以下密钥。开发者不会保存副本：\n\n'+key)
            self.reload()
        except Exception as exc: QMessageBox.warning(self,'恢复密钥',str(exc))

    def _export_package(self):
        suggested=f'ChromaticData_{time.strftime("%Y%m%d_%H%M%S")}.cadata'
        path,_=QFileDialog.getSaveFileName(self,'导出 Chromatic Analysis 数据包',suggested,'Chromatic Data Package (*.cadata)')
        if not path:return
        warning=QMessageBox.warning(self,'导出数据包','数据包包含账号、权限、色库和受管个人数据，当前版本未加密。\n请仅保存到受控位置并安全传输。\n\n继续导出吗？',QMessageBox.Yes|QMessageBox.Cancel,QMessageBox.Cancel)
        if warning!=QMessageBox.Yes:return
        try:
            out=export_portable_data_package(self.auth_store.path,self.library_store.path,path,organization_name=self.auth_store.organization_name())
            self.auth_store.log(self.current_user.username,'EXPORT_DATA_PACKAGE',str(out),f'installation={installation_info().get("installation_id","")}')
            self.reload(); QMessageBox.information(self,'导出完成',f'已创建数据包：\n{out}')
        except Exception as exc: QMessageBox.warning(self,'导出失败',str(exc))

    def _import_package(self):
        path,_=QFileDialog.getOpenFileName(self,'导入 Chromatic Analysis 数据包','','Chromatic Data Package (*.cadata)')
        if not path:return
        reply=QMessageBox.warning(self,'导入数据包','导入会替换当前组织的权限、色库、工作台、色卡和受管个人文件。\n系统会先自动备份当前数据库；本机 Installation ID 会保留。\n\n确定继续吗？',QMessageBox.Yes|QMessageBox.Cancel,QMessageBox.Cancel)
        if reply!=QMessageBox.Yes:return
        try:
            manifest=import_portable_data_package(path,self.auth_store.path,self.library_store.path)
            QMessageBox.information(self,'导入完成',f'数据已迁移到当前电脑。\n来源实例：{manifest.get("source_installation_label","")}\n镜像路径重建：{manifest.get("rebased_mirror_paths",0)} 条\n\n程序现在将退出，请重新运行 main.py。')
            QApplication.quit()
        except Exception as exc: QMessageBox.warning(self,'导入失败',str(exc))

    def _transfer(self):
        src=int(self.source_user.currentData() or 0); dst=int(self.target_user.currentData() or 0)
        if src<=0 or dst<=0 or src==dst:
            QMessageBox.information(self,'数据接管','请选择两个不同的用户。'); return
        users={u.user_id:u for u in self.auth_store.list_users()}; su=users.get(src); du=users.get(dst)
        if not su or not du:return
        reply=QMessageBox.warning(self,'数据接管',f'将 {su.display_name or su.username} 的个人工作台、色卡方案和受管个人文件整体转移给 {du.display_name or du.username}。\n\n管理员不会打开这些内容；此操作会写入审计日志。\n\n确定继续吗？',QMessageBox.Yes|QMessageBox.Cancel,QMessageBox.Cancel)
        if reply!=QMessageBox.Yes:return
        try:
            result=self.library_store.transfer_private_ownership(src,dst)
            files=transfer_managed_workfiles(src,su.username,dst,du.username)
            cleanup_readable_snapshots_for_user(src,su.username)
            rebuild_readable_snapshots(self.library_store.path,self.auth_store.path)
            detail=f'workbenches={result["workbenches"]}; color_cards={result["color_cards"]}; workfiles={files}'
            self.auth_store.log(self.current_user.username,'TRANSFER_PRIVATE_OWNERSHIP',f'{su.username}->{du.username}',detail)
            self.reload(); QMessageBox.information(self,'数据接管',f'已完成归属转移。\n{detail}')
        except Exception as exc: QMessageBox.warning(self,'数据接管失败',str(exc))

    def _backup(self):
        try:
            folder=create_database_backup(self.auth_store.path,self.library_store.path,include_business_files=True)
            self.auth_store.log(self.current_user.username,'BACKUP_MANUAL',str(folder),'DG4 full manual backup')
            self.reload(); QMessageBox.information(self,'备份完成',f'已创建本地备份：\n{folder}')
        except Exception as exc: QMessageBox.warning(self,'备份失败',str(exc))

    def _backup_color(self):
        try:
            folder=create_color_data_backup(self.library_store.path)
            self.auth_store.log(self.current_user.username,'BACKUP_COLOR_DATA',str(folder),'DG4 color domain')
            QMessageBox.information(self,'色彩数据备份',f'已备份色彩数据库及可读业务镜像：\n{folder}')
        except Exception as exc: QMessageBox.warning(self,'备份失败',str(exc))

    def _backup_security(self):
        try:
            folder=create_security_backup(self.auth_store.path)
            self.auth_store.log(self.current_user.username,'BACKUP_SECURITY',str(folder),'DG4 identity/permission domain')
            QMessageBox.information(self,'用户与权限备份',f'已备份用户、角色与权限数据库：\n{folder}')
        except Exception as exc: QMessageBox.warning(self,'备份失败',str(exc))

    def _archive_audit(self):
        try:
            folder=archive_audit_database()
            self.auth_store.log(self.current_user.username,'ARCHIVE_AUDIT',str(folder),'DG4 audit domain')
            QMessageBox.information(self,'审计归档',f'已创建审计数据库归档：\n{folder}')
        except Exception as exc: QMessageBox.warning(self,'归档失败',str(exc))

    def _migrate_business_root(self):
        target=QFileDialog.getExistingDirectory(self,'选择新的业务数据根目录',str(business_data_root().parent))
        if not target:return
        # Use a dedicated Chromatic Analysis Data folder when the selected path
        # is a parent directory, while respecting an explicitly named folder.
        chosen=Path(target)
        if chosen.name.casefold() != 'chromatic analysis data'.casefold():
            chosen=chosen/'Chromatic Analysis Data'
        reply=QMessageBox.warning(
            self,'迁移业务数据位置',
            f'将把业务数据复制到：\n{chosen}\n\n旧目录不会删除，可用于回滚。迁移完成后需要重新启动程序。\n\n继续吗？',
            QMessageBox.Yes|QMessageBox.Cancel,QMessageBox.Cancel)
        if reply!=QMessageBox.Yes:return
        try:
            result=migrate_business_data_root(chosen)
            rebase_library_mirror_paths(self.library_store.path)
            self.auth_store.log(self.current_user.username,'MIGRATE_BUSINESS_DATA_ROOT',result.get('new_root',''),f'files={result.get("files",0)}; old={result.get("old_root","")}')
            QMessageBox.information(self,'迁移完成',f'业务数据位置已更新。\n复制文件：{result.get("files",0)}\n\n旧目录保留：\n{result.get("old_root","")}\n\n程序现在将退出，请重新运行 main.py。')
            QApplication.quit()
        except Exception as exc:
            QMessageBox.warning(self,'迁移失败',str(exc))

    def _rebuild_readable_mirrors(self):
        try:
            result=rebuild_readable_snapshots(self.library_store.path,self.auth_store.path)
            mirror_result=self.library_store.rebuild_all_mirrors()
            rebased=rebase_library_mirror_paths(self.library_store.path)
            self.auth_store.log(self.current_user.username,'REBUILD_READABLE_MIRRORS','DG4',f'cards={result.get("color_cards",0)}; workbenches={result.get("workbenches",0)}; qtx={mirror_result.get("rebuilt",0)}/{mirror_result.get("total",0)}; failed={mirror_result.get("failed",0)}; rebased={rebased}')
            QMessageBox.information(self,'可读镜像',f'已重建：\n正式/官方 QTX {mirror_result.get("rebuilt",0)} / {mirror_result.get("total",0)} 个\n色卡方案 {result.get("color_cards",0)} 个\n比色工作台 {result.get("workbenches",0)} 个\n失败 {mirror_result.get("failed",0)} 个')
            self.reload()
        except Exception as exc:
            QMessageBox.warning(self,'重建失败',str(exc))

    def _check_storage_health(self):
        try:
            report=storage_health_report()
            lines=[f'DG4 布局版本：{report.get("layout_version","")}',
                   f'业务数据：{report.get("business_root","")}',f'系统数据：{report.get("system_root","")}','']
            labels={'security':'用户与权限','color':'色彩数据库','audit':'审计数据库'}
            all_ok=True
            for key,item in report.get('databases',{}).items():
                ok=bool(item.get('ok')); all_ok=all_ok and ok
                lines.append(f'{labels.get(key,key)}：{"正常" if ok else "异常"}  ·  {item.get("message","")}')
            orphan=report.get('orphan_owner_ids',[]) or []
            if orphan: all_ok=False
            expected_cards=int(report.get('expected_color_cards',0) or 0)
            actual_cards=int(report.get('color_card_files',0) or 0)
            cards_ok=bool(report.get('color_card_snapshot_complete',False))
            if not cards_ok: all_ok=False
            legacy_dirs=report.get('legacy_color_card_dirs',[]) or []
            if legacy_dirs: all_ok=False
            legacy=report.get('legacy_customers',{}) or {}
            mirror_refs=int(legacy.get('active_mirror_refs',0) or 0)
            if mirror_refs: all_ok=False
            gov=report.get('color_card_governance',{}) or {}
            lines.extend([
                '',
                f'正式色库 QTX：{report.get("formal_library_files",0)}',
                f'官方色库 QTX：{report.get("official_library_files",0)}',
                f'色卡可读镜像：{actual_cards} / 数据库方案 {expected_cards}  ·  {"正常" if cards_ok else "不完整"}',
                f'色卡治理：仅自己 {gov.get("private",0)} · 共享 {gov.get("organization",0)} · 已发布 {gov.get("published",0)} · 历史未归属 {gov.get("legacy_unassigned",0)}',
                f'个人工作区文件：{report.get("personal_files",0)}',
                '',
                '旧 Customers 兼容状态：',
                f'  目录：{"保留" if legacy.get("exists") else "不存在"}  ·  文件 {legacy.get("files",0)}',
                f'  活动镜像引用：{mirror_refs}（应为 0）',
                f'  历史逻辑路径引用：{legacy.get("logical_source_refs",0)}（允许，仅用于兼容旧记录/收藏）',
                f'  首次归档副本：{legacy.get("archive_copy") or "未创建/无旧目录"}',
            ])
            if legacy_dirs:
                lines.append('仍发现旧机器ID色卡目录：'+', '.join(map(str,legacy_dirs)))
            if orphan: lines.append(f'需要管理员处理的孤立数据归属：用户ID {", ".join(map(str,orphan))}')
            QMessageBox.information(self,'数据完整性检查' if all_ok else '数据完整性检查 · 发现异常','\n'.join(lines))
        except Exception as exc:
            QMessageBox.warning(self,'检查失败',str(exc))

    def _restore_color(self):
        folder=QFileDialog.getExistingDirectory(self,'选择色彩数据备份目录',str(business_backup_root()/'色彩数据'))
        if not folder:return
        reply=QMessageBox.warning(self,'恢复色彩数据','只恢复色彩数据库、正式色库镜像、色卡方案和个人工作数据；不会修改用户账号、角色、权限或审计记录。\n程序会先创建完整安全备份，然后退出。\n\n继续吗？',QMessageBox.Yes|QMessageBox.Cancel,QMessageBox.Cancel)
        if reply!=QMessageBox.Yes:return
        try:
            restore_color_data_backup(folder,self.library_store.path,self.auth_store.path)
            QMessageBox.information(self,'恢复完成','色彩数据已恢复。程序现在将退出，请重新运行 main.py。')
            QApplication.quit()
        except Exception as exc: QMessageBox.warning(self,'恢复失败',str(exc))

    def _restore_security(self):
        folder=QFileDialog.getExistingDirectory(self,'选择用户与权限备份目录',str(security_backup_root()))
        if not folder:return
        reply=QMessageBox.warning(self,'恢复用户与权限','只恢复用户、角色和权限；不会修改色彩数据库、正式色库、色卡方案或审计记录。\n程序会先创建安全备份，然后退出。\n\n继续吗？',QMessageBox.Yes|QMessageBox.Cancel,QMessageBox.Cancel)
        if reply!=QMessageBox.Yes:return
        try:
            restore_security_backup(folder,self.auth_store.path,self.library_store.path)
            QMessageBox.information(self,'恢复完成','用户与权限已恢复。程序现在将退出，请重新运行 main.py。')
            QApplication.quit()
        except Exception as exc: QMessageBox.warning(self,'恢复失败',str(exc))

    def _restore(self):
        folder=QFileDialog.getExistingDirectory(self,'选择 Chromatic Analysis 备份目录',str(backup_root()))
        if not folder:return
        reply=QMessageBox.warning(self,'恢复备份','完整恢复会替换用户权限、色彩数据库、审计记录以及备份中包含的业务镜像。\n系统会先自动备份当前数据，然后退出程序。\n\n确定继续吗？',QMessageBox.Yes|QMessageBox.Cancel,QMessageBox.Cancel)
        if reply!=QMessageBox.Yes:return
        try:
            restore_database_backup(folder,self.auth_store.path,self.library_store.path)
            QMessageBox.information(self,'恢复完成','备份已恢复。程序现在将退出，请重新运行 main.py。')
            QApplication.quit()
        except Exception as exc: QMessageBox.warning(self,'恢复失败',str(exc))

    def _open(self,path:Path):
        import os, subprocess
        try: os.startfile(str(path))
        except Exception:
            try: subprocess.Popen(['xdg-open',str(path)])
            except Exception: QMessageBox.information(self,'目录',str(path))
