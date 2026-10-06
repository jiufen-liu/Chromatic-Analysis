from __future__ import annotations

import ctypes
import json
import math
import os
import shutil
import statistics
import subprocess
import sys
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PERF_DIR = ROOT / 'performance_reports'


def _pct(values, q):
    if not values:
        return 0.0
    data = sorted(float(x) for x in values)
    if len(data) == 1:
        return data[0]
    pos = (len(data)-1)*q
    lo = int(pos); hi = min(lo+1,len(data)-1); f=pos-lo
    return data[lo]*(1-f)+data[hi]*f


def _appdata_dir() -> Path | None:
    appdata = os.environ.get('APPDATA')
    return (Path(appdata) / 'QTX 色彩分析') if appdata else None


class WindowsProcessSampler:
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    PROCESS_VM_READ = 0x0010

    class FILETIME(ctypes.Structure):
        _fields_ = [('dwLowDateTime', wintypes.DWORD), ('dwHighDateTime', wintypes.DWORD)]

    class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
        _fields_ = [
            ('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD),
            ('PeakWorkingSetSize', ctypes.c_size_t), ('WorkingSetSize', ctypes.c_size_t),
            ('QuotaPeakPagedPoolUsage', ctypes.c_size_t), ('QuotaPagedPoolUsage', ctypes.c_size_t),
            ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t), ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
            ('PagefileUsage', ctypes.c_size_t), ('PeakPagefileUsage', ctypes.c_size_t),
            ('PrivateUsage', ctypes.c_size_t),
        ]

    def __init__(self, pid: int):
        self.pid = int(pid)
        self.kernel32 = ctypes.windll.kernel32
        self.psapi = ctypes.windll.psapi
        self.handle = self.kernel32.OpenProcess(
            self.PROCESS_QUERY_LIMITED_INFORMATION | self.PROCESS_VM_READ, False, self.pid
        )
        self.prev_cpu = None
        self.prev_wall = None
        self.cpu_count = max(1, os.cpu_count() or 1)

    @staticmethod
    def _ft_value(ft):
        return (int(ft.dwHighDateTime) << 32) | int(ft.dwLowDateTime)

    def sample(self):
        if not self.handle:
            return None
        mem = self.PROCESS_MEMORY_COUNTERS_EX()
        mem.cb = ctypes.sizeof(mem)
        ok_mem = self.psapi.GetProcessMemoryInfo(self.handle, ctypes.byref(mem), mem.cb)
        creation=self.FILETIME(); exit_=self.FILETIME(); kernel=self.FILETIME(); user=self.FILETIME()
        ok_cpu = self.kernel32.GetProcessTimes(self.handle, ctypes.byref(creation), ctypes.byref(exit_), ctypes.byref(kernel), ctypes.byref(user))
        now=time.perf_counter()
        cpu_pct=None
        if ok_cpu:
            cpu_100ns=self._ft_value(kernel)+self._ft_value(user)
            cpu_s=cpu_100ns/10_000_000.0
            if self.prev_cpu is not None and self.prev_wall is not None:
                wall=max(1e-6,now-self.prev_wall)
                cpu_pct=max(0.0,(cpu_s-self.prev_cpu)/wall*100.0/self.cpu_count)
            self.prev_cpu=cpu_s; self.prev_wall=now
        return {
            't': now,
            'working_set_mb': (mem.WorkingSetSize/1024/1024) if ok_mem else None,
            'private_mb': (mem.PrivateUsage/1024/1024) if ok_mem else None,
            'peak_working_set_mb': (mem.PeakWorkingSetSize/1024/1024) if ok_mem else None,
            'cpu_pct_total_capacity': cpu_pct,
        }

    def close(self):
        if self.handle:
            self.kernel32.CloseHandle(self.handle)
            self.handle=None


def _grade(ui: dict, resource: dict, operations: dict) -> tuple[str, list[str]]:
    reasons=[]
    ev=ui.get('event_loop',{}) if ui else {}
    inter=ui.get('interaction',{}) if ui else {}
    p95=float(inter.get('response_p95_ms') or 0)
    max_stall=float(ev.get('max_delay_ms') or 0)
    over500=int(ev.get('over_500_ms') or 0)
    severe_ops=[]
    for name,row in operations.items():
        if float(row.get('max_ms',0) or 0)>=1000:
            severe_ops.append((name,float(row.get('max_ms',0))))
    if max_stall>=1000 or over500>=5 or p95>=500:
        grade='需要优化'
    elif max_stall>=500 or over500>0 or p95>=250 or severe_ops:
        grade='基本可用，有明显卡顿点'
    elif max_stall>=250 or p95>=150:
        grade='流畅度良好，存在轻微等待'
    else:
        grade='流畅'
    if p95: reasons.append(f'交互响应 P95 {p95:.1f} ms')
    reasons.append(f'事件循环最大阻塞 {max_stall:.1f} ms')
    if severe_ops:
        reasons.append('≥1s 热点: '+', '.join(f'{n} {v:.0f}ms' for n,v in sorted(severe_ops,key=lambda x:-x[1])[:5]))
    peak=resource.get('peak_working_set_mb')
    if peak: reasons.append(f'峰值工作集 {peak:.1f} MB')
    return grade,reasons


def _print_route():
    print(r'''
==============================================================================
Chromatic Analysis · 全程序流畅度 / 交互性能回归测试
==============================================================================
这不是“单一算法 benchmark”。程序会正常打开，请按真实工作方式操作。
工具会记录：启动时间、UI事件循环阻塞、点击到刷新近似响应、CPU/内存、已有热点计时。

建议固定走一遍以下路线（同一台电脑、同一批测试文件，后续版本都重复）：

  A. 启动 / MDI
     - 启动程序；新建2~3个内部窗口；最小化/最大化/恢复/关闭

  B. 色库
     - 打开 Coloro 3500；首页/翻页；搜索名称/LAB；Ctrl+A；取消选择
     - 打开正式色库/官方色库；拖色卡到比色工作台/色卡编排

  C. 比色工作台
     - 导入常用大QTX；快速切换多条批次样
     - 数据表 ↔ 综合分析反复切换；切 D65/F11；切 10°/2°；切色差公式
     - 观察反射率、ΔR、a*b*容差图是否即时跟随

  D. 查色 / 找色
     - 选1个标准色查询；改变范围/结果数/容差；多选结果；清空选择

  E. 色卡编排
     - 导入 Adidas 等常用QTX
     - 分别执行：L*/a*/b*/C*/h*、Munsell H、光谱+感知
     - 搜索、全选、空位删除、复制/拖入新窗口

  F. 3D LAB
     - 打开中/大型色库点云；连续旋转10秒、右键平移、滚轮缩放
     - 点大小/透明度滑块连续拖动；点击色块联动；最小化/恢复

  G. 文件与导出
     - QTX / CPX / Excel 导入；色卡编排导出 Excel/图片/PDF（按你常用流程）
     - 如方便，做一次保存/另存为/导出

完成后【正常关闭主程序】。不要直接关这个黑色测试窗口。
==============================================================================
''')


def main():
    PERF_DIR.mkdir(exist_ok=True)
    stamp=datetime.now().strftime('%Y%m%d_%H%M%S')
    session_dir=PERF_DIR/f'FULL_APP_SESSION_{stamp}'
    session_dir.mkdir(parents=True,exist_ok=True)
    ui_path=session_dir/'ui_probe.json'
    report_json=session_dir/'FULL_APP_PERFORMANCE.json'
    report_txt=session_dir/'FULL_APP_PERFORMANCE.txt'

    _print_route()
    input('准备好后按 Enter 启动程序...')

    env=os.environ.copy()
    env['CHROMATIC_UI_PERF_PROBE']='1'
    env['CHROMATIC_UI_PERF_REPORT']=str(ui_path)
    env['CHROMATIC_UI_PERF_INTERVAL_MS']='50'
    env.setdefault('CHROMATIC_PERF_THRESHOLD_MS','5')

    started=time.perf_counter()
    proc=subprocess.Popen([sys.executable,str(ROOT/'main.py')],cwd=str(ROOT),env=env)
    sampler=WindowsProcessSampler(proc.pid) if os.name=='nt' else None
    samples=[]
    try:
        while proc.poll() is None:
            if sampler:
                row=sampler.sample()
                if row: samples.append(row)
            time.sleep(1.0)
    except KeyboardInterrupt:
        print('\n收到 Ctrl+C；请先正常关闭应用。')
        try: proc.wait(timeout=30)
        except Exception: pass
    finally:
        if sampler: sampler.close()
    duration=time.perf_counter()-started

    # Give normal-exit atexit writers a brief moment to flush.
    time.sleep(0.4)
    ui={}
    if ui_path.exists():
        try: ui=json.loads(ui_path.read_text(encoding='utf-8'))
        except Exception: pass

    appdata=_appdata_dir()
    perf_summary={}
    perf_summary_src=appdata/'performance_summary.json' if appdata else None
    perf_log_src=appdata/'performance.log' if appdata else None
    if perf_summary_src and perf_summary_src.exists():
        try:
            perf_summary=json.loads(perf_summary_src.read_text(encoding='utf-8'))
            shutil.copy2(perf_summary_src,session_dir/'performance_summary.json')
        except Exception: pass
    if perf_log_src and perf_log_src.exists():
        try: shutil.copy2(perf_log_src,session_dir/'performance.log')
        except Exception: pass

    ws=[x['working_set_mb'] for x in samples if x.get('working_set_mb') is not None]
    priv=[x['private_mb'] for x in samples if x.get('private_mb') is not None]
    cpu=[x['cpu_pct_total_capacity'] for x in samples if x.get('cpu_pct_total_capacity') is not None]
    resource={
        'samples':len(samples),
        'peak_working_set_mb':round(max(ws),2) if ws else None,
        'working_set_p95_mb':round(_pct(ws,.95),2) if ws else None,
        'peak_private_mb':round(max(priv),2) if priv else None,
        'cpu_avg_pct_total_capacity':round(statistics.mean(cpu),2) if cpu else None,
        'cpu_p95_pct_total_capacity':round(_pct(cpu,.95),2) if cpu else None,
        'cpu_peak_pct_total_capacity':round(max(cpu),2) if cpu else None,
    }
    ops=perf_summary.get('operations',{}) if isinstance(perf_summary,dict) else {}
    grade,reasons=_grade(ui,resource,ops)

    top_max=sorted(ops.items(),key=lambda kv:float(kv[1].get('max_ms',0) or 0),reverse=True)[:15]
    top_avg=sorted(ops.items(),key=lambda kv:float(kv[1].get('average_ms',0) or 0),reverse=True)[:15]
    report={
        'schema':1,
        'captured_at':datetime.now().isoformat(timespec='seconds'),
        'duration_s':round(duration,2),
        'exit_code':proc.returncode,
        'grade':grade,
        'grade_reasons':reasons,
        'ui':ui,
        'resource':resource,
        'hotspots_by_max':[{'operation':n,**r} for n,r in top_max],
        'hotspots_by_average':[{'operation':n,**r} for n,r in top_avg],
        'session_dir':str(session_dir),
    }
    report_json.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')

    ev=ui.get('event_loop',{}) if ui else {}
    inter=ui.get('interaction',{}) if ui else {}
    startup=ui.get('startup',{}) if ui else {}
    lines=[
        'Chromatic Analysis · 全程序流畅度 / 交互性能报告',
        '='*72,
        f'测试时间: {report["captured_at"]}',
        f'会话时长: {duration:.1f} s',
        f'程序退出码: {proc.returncode}',
        f'综合评价: {grade}',
        '',
        '【核心指标】',
        f'主窗口显示: {startup.get("main_window_show_ms")} ms',
        f'主窗口首次绘制: {startup.get("main_window_first_paint_ms")} ms',
        f'交互响应 P50 / P95 / P99 / Max: {inter.get("response_p50_ms",0)} / {inter.get("response_p95_ms",0)} / {inter.get("response_p99_ms",0)} / {inter.get("response_max_ms",0)} ms',
        f'事件循环延迟 P95 / P99 / Max: {ev.get("delay_p95_ms",0)} / {ev.get("delay_p99_ms",0)} / {ev.get("max_delay_ms",0)} ms',
        f'明显阻塞 >100/250/500/1000ms: {ev.get("over_100_ms",0)} / {ev.get("over_250_ms",0)} / {ev.get("over_500_ms",0)} / {ev.get("over_1000_ms",0)}',
        f'峰值工作集: {resource.get("peak_working_set_mb")} MB',
        f'CPU 平均/P95/峰值(占整机总容量): {resource.get("cpu_avg_pct_total_capacity")} / {resource.get("cpu_p95_pct_total_capacity")} / {resource.get("cpu_peak_pct_total_capacity")} %',
        '',
        '【评价依据】',
    ]
    lines += [f'- {x}' for x in reasons] or ['- 无']
    lines += ['', '【最慢操作 Top 15（按单次最大耗时）】']
    if top_max:
        for n,r in top_max:
            lines.append(f'- {n}: max {r.get("max_ms",0):.1f} ms | avg {r.get("average_ms",0):.1f} ms | count {r.get("count",0)} | slow {r.get("slow_count",0)}')
    else:
        lines.append('- 本次未捕获到已埋点热点；仍可依据 UI 阻塞指标判断整体流畅度。')
    lines += ['', '【解释】',
              '- 交互响应：用户鼠标/键盘输入到下一次界面绘制的近似延迟，用于发现“点了没反应”。',
              '- 事件循环阻塞：Qt 主线程被同步任务占住的时间，是“整个窗口卡住”的关键指标。',
              '- 反复使用同一台电脑、同一组测试文件、同一路线，版本间才可直接比较。',
              '', f'报告目录: {session_dir}']
    report_txt.write_text('\n'.join(lines),encoding='utf-8')

    print('\n'+'='*72)
    print(f'测试完成：{grade}')
    for x in reasons: print(' - '+x)
    print(f'\n报告：\n{report_txt}\n{report_json}')
    print('\n把整个 FULL_APP_SESSION_* 文件夹（或之后用诊断中心打包）发给我，就能定位具体慢点。')
    return int(proc.returncode or 0)


if __name__=='__main__':
    raise SystemExit(main())
