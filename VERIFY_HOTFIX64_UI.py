from pathlib import Path

ROOT=Path(__file__).resolve().parent
main=(ROOT/'qtx_app'/'main_window.py').read_text(encoding='utf-8')
quick=(ROOT/'qtx_app'/'lab3d_quick.py').read_text(encoding='utf-8')

def need(cond,msg):
    if not cond: raise SystemExit('[FAIL] '+msg)

need("QTimer.singleShot(0,self.arrange_primary_tool_windows)" not in main,
     'old 70/30 automatic primary-window tiling is still active')
need("min_sizes={0:(720,480),1:(860,540),2:(700,480),3:(680,460),4:(700,480)}" in main,
     'tool-window usable minimum sizes missing')
need("self.point.setSingleStep(1); self.point.setPageStep(1)" in quick,
     '3D point-size slider page step is not fixed')
need("'_draft':True" in main and "直接关闭将不进入已有方案" in main,
     'new palette is not a transient draft')
need("self.card.pop('_draft',None)" in main,
     'saved palette does not clear draft marker')
need("for card in cards[:6]" in main and "查看全部方案" in main,
     'saved palette menu is not capped/searchable')
need("搜索方案名称" in main,
     'palette manager search field missing')
print('[PASS] Hotfix64 UI stability static checks passed.')
