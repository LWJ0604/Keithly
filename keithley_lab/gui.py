"""Korean-friendly Tk desktop. All acquisition runs on one bounded worker."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from .engine import Controller, ROLES, defaults, validate, estimate, now, app_root
from .mock import MockKXCI
from .protocol import KXCI, EXPECTED_ID

BG, PANEL, INK, MUTED, ACCENT = '#f1f5f7', '#ffffff', '#16313d', '#526b78', '#087f8c'
ROLE_KO = {'drain': '드레인', 'source': '소스', 'gate': '백 게이트'}


class ScrollPanel(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, style='Card.TFrame')
        self.canvas = tk.Canvas(self, bg=PANEL, highlightthickness=0, width=410, height=380)
        scrollbar = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        scrollbar.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.inner = ttk.Frame(self.canvas, style='Card.TFrame', padding=14)
        item = self.canvas.create_window((0, 0), window=self.inner, anchor='nw')
        self.inner.bind('<Configure>', lambda event:self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda event:self.canvas.itemconfigure(item, width=event.width))


class App:
    def __init__(self, root, workspace=None):
        self.root = root
        self.workspace = Path(workspace or app_root())
        self.controller = Controller()
        self.rows = []
        self.vars = {}
        self.inputs = []
        self.preflight_window = None
        self.probing = False
        self.closing = False
        self.last_path = None
        self.total_points = 0
        self.root.title('Keithley Lab · 4200-SCS | 시뮬레이션 기본 시작')
        # Stable sizing on research desktops with mixed Windows DPI settings.
        self.root.tk.call('tk', 'scaling', 1.333333)
        self.root.geometry('1220x810+40+15')
        self.root.minsize(1080, 730)
        self.root.configure(bg=BG)
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('.', font=('맑은 고딕', 10), background=PANEL, foreground=INK)
        style.configure('TFrame', background=BG)
        style.configure('Card.TFrame', background=PANEL)
        style.configure('TLabel', background=PANEL, foreground=INK)
        style.configure('Muted.TLabel', foreground=MUTED)
        style.configure('TButton', padding=(12, 7), background='#e0edf0', foreground=INK, borderwidth=0)
        style.map('TButton', background=[('active','#c4e2e7'),('disabled','#e7ecef')], foreground=[('disabled','#849399')])
        style.configure('Accent.TButton', background=ACCENT, foreground='white')
        style.map('Accent.TButton', background=[('active','#0c9cab'),('disabled','#aacccc')])
        style.configure('Stop.TButton', background='#94374a', foreground='white')
        style.configure('TEntry', fieldbackground='#ffffff', foreground=INK, insertcolor=INK, padding=5)
        style.configure('TCombobox', fieldbackground='#ffffff', foreground=INK, padding=4)
        style.map('TCombobox', fieldbackground=[('readonly','#ffffff')], selectbackground=[('readonly','#ffffff')], selectforeground=[('readonly',INK)])
        style.configure('TNotebook', background=BG, borderwidth=0)
        style.configure('TNotebook.Tab', padding=(15,8), background='#e3edef', foreground=MUTED)
        style.map('TNotebook.Tab', background=[('selected',PANEL)], foreground=[('selected',INK)])
        style.configure('Treeview', fieldbackground=PANEL, background=PANEL, foreground=INK, rowheight=28)
        style.configure('Treeview.Heading', background='#e0edf0', foreground=INK)
        style.configure('TCheckbutton', background=PANEL, foreground=INK)
        style.configure('Horizontal.TProgressbar', background=ACCENT, troughcolor='#dce8eb', borderwidth=0)
        header = tk.Frame(root, bg=BG)
        header.pack(fill='x', padx=24, pady=(10,8))
        tk.Label(header, text='KEITHLEY  /  LAB', bg=BG, fg=ACCENT, font=('Segoe UI', 11,'bold')).pack(anchor='w')
        tk.Label(header, text='4200-SCS 측정 워크벤치', bg=BG, fg=INK, font=('맑은 고딕', 23,'bold')).pack(anchor='w')
        tk.Label(header, text='KXCI V8.2-SP4  ·  수동 실행  ·  로컬 데이터  ·  자동 재접속 없음', bg=BG, fg=MUTED, font=('맑은 고딕', 10)).pack(anchor='w',pady=(3,0))
        connection = ttk.Frame(root, style='Card.TFrame', padding=8)
        connection.pack(fill='x',padx=24,pady=(0,12))
        self.combo(connection,'mode','simulation',['simulation','real'],0,0,width=13)
        self.entry(connection,'host','192.0.2.1',0,1,width=16)
        self.entry(connection,'port','1225',0,2,width=7)
        self.probe_button = ttk.Button(connection,text='연결 / ID 확인',command=self.probe)
        self.probe_button.grid(row=0,column=3,padx=8)
        self.mode_badge = ttk.Label(connection,text='SIMULATION · 합성 데이터',foreground=ACCENT)
        self.mode_badge.grid(row=0,column=4,padx=8,sticky='w')
        self.identity = ttk.Label(connection,text='미연결 · 시작 시 연결하지 않습니다',style='Muted.TLabel')
        self.identity.grid(row=1,column=0,columnspan=5,sticky='w',pady=(6,0))
        self.vars['mode'].trace_add('write', lambda *a:self.mode_changed())
        body = ttk.Frame(root)
        body.pack(fill='both',expand=True,padx=24)
        body.columnconfigure(0,weight=0,minsize=450)
        body.columnconfigure(1,weight=1)
        body.rowconfigure(0,weight=1)
        self.tabs = ttk.Notebook(body)
        self.tabs.grid(row=0,column=0,sticky='nsew',padx=(0,14))
        panels = [ScrollPanel(self.tabs) for _ in range(4)]
        setup, timing, channels, conditions = [panel.inner for panel in panels]
        for panel, title in zip(panels, ['스윕','시간','SMU / 한계','시료 / 저장']):
            self.tabs.add(panel, text=title)
        ttk.Label(setup,text='01  측정과 스윕',font=('맑은 고딕',12,'bold')).grid(row=0,column=0,columnspan=3,sticky='w',pady=(0,10))
        self.labeled(setup,'측정 종류','workflow','Spot',1,values=['Spot','Id-Vd','Id-Vg'])
        self.vars['workflow'].trace_add('write',lambda *a:self.workflow_changed())
        for i,role in enumerate(ROLES):
            self.labeled(setup,ROLE_KO[role]+' 고정 전압 (V)',role+'_fixed', '.05' if role=='drain' else '0',2+i)
        self.labeled(setup,'패밀리 고정 전압 목록 (V)','family','0',5)
        ttk.Label(setup,text='Id–Vd: 게이트 목록  /  Id–Vg: 드레인 목록\n쉼표 구분 · 목록 순서 → forward → reverse',style='Muted.TLabel').grid(row=6,column=0,columnspan=3,sticky='w',pady=4)
        sweep = ttk.Frame(setup,style='Card.TFrame')
        sweep.grid(row=7,column=0,columnspan=3,sticky='ew',pady=5)
        for i,(key,label,val) in enumerate([('start','시작 V','0'),('stop','끝 V','.05'),('step','부호 있는 step V','.01')]):
            ttk.Label(sweep,text=label,style='Muted.TLabel').grid(row=0,column=i,sticky='w')
            self.entry(sweep,key,val,1,i,width=12)
        self.labeled(setup,'방향','direction','ascending',8,values=['ascending','descending'])
        self.vars['return'] = tk.BooleanVar(value=False)
        cb = ttk.Checkbutton(setup,text='왕복 스윕 · 전환점 중복 없음',variable=self.vars['return'])
        cb.grid(row=9,column=0,columnspan=3,sticky='w',pady=6)
        self.inputs.append((cb,'normal'))
        ttk.Label(timing,text='시간과 적분',font=('맑은 고딕',12,'bold')).grid(row=0,column=0,columnspan=3,sticky='w',pady=(0,12))
        self.labeled(timing,'곡선 첫 바이어스 hold (s)','hold','.2',1)
        self.labeled(timing,'매 point 읽기 전 delay (s)','delay','.1',2)
        self.labeled(timing,'적분 IT  ·  1 / 2 / 3','integration','2',3,values=['1','2','3'])
        self.labeled(timing,'전원 주파수 (Hz)','line','60',4,values=['50','60'])
        self.labeled(timing,'명령 timeout (s)','timeout','3',5)
        ttk.Label(timing,text='IT1: 0.1 PLC · IT2: 1 PLC · IT3: 10 PLC\n\n1. 새 곡선: zero 후 첫 바이어스 설정\n2. Hold: 곡선당 한 번 대기\n3. Delay: 각 point 바이어스 응답/SP 이후 대기\n4. 전류 읽기: 드레인 → 소스 → 게이트 순차\n5. point 저장 후 다음 전압으로 이동\n\n왕복 분기에서는 hold를 반복하지 않습니다.\n총시간은 hold + delay + 적분 + 통신/저장 시간입니다.\n실제 대기·측정 경과시간은 CSV에 별도로 기록됩니다.\n장비 정밀 타이머 기반 스윕이 아닙니다.',wraplength=395,style='Muted.TLabel').grid(row=6,column=0,columnspan=3,sticky='w',pady=16)
        ttk.Label(channels,text='02  배선과 소자 승인 한계',font=('맑은 고딕',12,'bold')).grid(row=0,column=0,columnspan=4,sticky='w',pady=(0,12))
        for i,role in enumerate(ROLES):
            base=1+i*4
            ttk.Label(channels,text=ROLE_KO[role],foreground=ACCENT).grid(row=base,column=0,sticky='w')
            self.combo(channels,role+'_channel',str(i+1),['1','2','3'],base,1,width=6)
            ttk.Label(channels,text='SMU 번호').grid(row=base,column=2,sticky='w')
            ttk.Label(channels,text='최소 V / 최대 V').grid(row=base+1,column=0,sticky='w')
            self.entry(channels,role+'_min','0',base+1,1,width=8)
            self.entry(channels,role+'_max','.05' if role=='drain' else '0',base+1,2,width=8)
            ttk.Label(channels,text='Compliance (µA)').grid(row=base+2,column=0,sticky='w')
            self.entry(channels,role+'_comp','100',base+2,1,width=8)
        ttk.Label(channels,text='현재 확인된 배선: D=SMU1 / S=SMU2 / BG=SMU3\nKCON: 모든 채널 SMU 기능, 번호 일치.\n앱 매핑은 전극 역할만 변경하며 KCON은 수정하지 않습니다.',wraplength=395,style='Muted.TLabel').grid(row=14,column=0,columnspan=4,sticky='w',pady=12)
        ttk.Label(channels,text='실제로 시험한 바이어스: D +0.05 V, S/BG 0 V.\n기본 한계는 D 0…0.05 V / S,BG 0 V입니다.\n넓은 범위는 별도 소자 검토가 필요합니다.\n\n앱 상한: ±20 V, 100 µA (안전 보증 아님).\n매뉴얼 전압 명령 범위 ±210 V는 소자 승인 한계가 아닙니다.\n최소 compliance 100 nA: PreAmp 설치 여부 미확인.',wraplength=395,foreground='#a15b06').grid(row=15,column=0,columnspan=4,sticky='w')
        ttk.Label(conditions,text='03  수동 조건과 데이터',font=('맑은 고딕',12,'bold')).grid(row=0,column=0,columnspan=3,sticky='w',pady=(0,12))
        self.labeled(conditions,'시료 이름 (선택)','sample','',1)
        for i,(key,label) in enumerate([('illumination','광 조건'),('temperature','온도 조건')]):
            self.labeled(conditions,label+' 확인','%s_status'%key,'unknown',2+i*2,values=['unknown','confirmed'])
            self.labeled(conditions,label+' 수동 입력','%s_desc'%key,'',3+i*2)
        self.labeled(conditions,'메모 (선택)','notes','',6)
        ttk.Label(conditions,text='저장 폴더 (절대 경로)',style='Muted.TLabel').grid(row=7,column=0,columnspan=3,sticky='w',pady=(14,3))
        self.entry(conditions,'save_root',str(self.workspace/'data'),8,0,width=39,columnspan=3)
        browse=ttk.Button(conditions,text='폴더 선택…',command=self.browse)
        browse.grid(row=9,column=0,columnspan=3,sticky='w',pady=8)
        self.inputs.append((browse,'normal'))
        ttk.Label(conditions,text='SIMULATION / REAL 폴더를 자동 분리합니다.\n매 point CSV 저장 · 설정 JSON · 명령 기록 · SVG 그래프\n기존 파일을 덮어쓰지 않는 고유 실행 폴더를 만듭니다.\nOneDrive 동기화 상태는 앱이 보장하지 않습니다.\n\n광원 / 레이저 / 온도 장비 제어는 제공하지 않습니다.\nunknown은 미확인 상태 그대로 기록됩니다.',wraplength=390,style='Muted.TLabel').grid(row=10,column=0,columnspan=3,sticky='w',pady=10)
        right=ttk.Frame(body,style='Card.TFrame',padding=14)
        right.grid(row=0,column=1,sticky='nsew')
        top=ttk.Frame(right,style='Card.TFrame'); top.pack(fill='x')
        ttk.Label(top,text='LIVE  /  드레인 전류',font=('맑은 고딕',12,'bold')).pack(side='left')
        self.current_label=ttk.Label(top,text='— A',foreground=ACCENT,font=('Consolas',20,'bold')); self.current_label.pack(side='right')
        self.canvas=tk.Canvas(right,bg='#ffffff',highlightthickness=0,height=200)
        self.canvas.pack(fill='both',expand=True,pady=(10,8))
        self.canvas.bind('<Configure>',lambda e:self.draw_plot())
        self.progress=ttk.Progressbar(right,mode='determinate');self.progress.pack(fill='x')
        self.progress_label=ttk.Label(right,text='0 points  ·  검토 후 실행',style='Muted.TLabel');self.progress_label.pack(anchor='w',pady=6)
        self.status_label=ttk.Label(right,text='준비됨 · 실제 장비 미연결',wraplength=590,foreground=ACCENT); self.status_label.pack(anchor='w',pady=(0,8))
        history_label=ttk.Label(right,text='실행 기록  /  선택한 저장 폴더',style='Muted.TLabel')
        history_label.pack(anchor='w')
        self.history=ttk.Treeview(right,columns=('mode','result','points'),height=3,show='tree headings')
        self.history.heading('#0',text='Run ID'); self.history.column('#0',width=220)
        for key,label,w in [('mode','모드',75),('result','결과',110),('points','점수',55)]:
            self.history.heading(key,text=label);self.history.column(key,width=w)
        self.history.pack(fill='x',pady=5)
        historybar=ttk.Frame(right,style='Card.TFrame');historybar.pack(fill='x')
        ttk.Button(historybar,text='기록 새로고침',command=self.refresh_history).pack(side='left')
        ttk.Button(historybar,text='선택 폴더 열기',command=self.open_history).pack(side='left',padx=6)
        # Reserve controls first and let only the plot absorb smaller screen heights.
        for child in right.winfo_children():child.pack_forget()
        right.columnconfigure(0,weight=1)
        right.rowconfigure(1,weight=1)
        self.canvas.config(height=140)
        for row,widget in enumerate((top,self.canvas,self.progress,self.progress_label,self.status_label,history_label,self.history,historybar)):
            widget.grid(row=row,column=0,sticky='nsew' if row==1 else 'ew',pady=(3,3))
        footer=ttk.Frame(root,padding=(24,12));footer.pack(side='bottom',fill='x',before=body)
        self.review_button=ttk.Button(footer,text='설정 검토 → 시작',style='Accent.TButton',command=self.review);self.review_button.pack(side='left')
        self.stop_button=ttk.Button(footer,text='중단 / 정리',style='Stop.TButton',command=self.stop,state='disabled');self.stop_button.pack(side='left',padx=10)
        ttk.Label(footer,text='ACK ≠ 실제 출력 확인  ·  연결 손실 시 장비 현장 확인 필요',background=BG,foreground='#a15b06').pack(side='right')
        self.workflow_changed()
        self.refresh_history()
        self.poll_id = self.root.after(80,self.poll)

    def entry(self,parent,key,value,row,col,width=16,columnspan=1):
        self.vars[key]=tk.StringVar(value=value)
        widget=ttk.Entry(parent,textvariable=self.vars[key],width=width)
        widget.grid(row=row,column=col,columnspan=columnspan,sticky='ew',padx=(0,8),pady=3)
        self.inputs.append((widget,'normal'))
        return widget

    def combo(self,parent,key,value,values,row,col,width=18):
        self.vars[key]=tk.StringVar(value=value)
        widget=ttk.Combobox(parent,textvariable=self.vars[key],values=values,state='readonly',width=width)
        widget.grid(row=row,column=col,sticky='ew',padx=(0,8),pady=3)
        self.inputs.append((widget,'readonly'))
        return widget

    def labeled(self,parent,label,key,value,row,values=None):
        ttk.Label(parent,text=label).grid(row=row,column=0,sticky='w',padx=(0,12),pady=3)
        if values:
            return self.combo(parent,key,value,values,row,1)
        return self.entry(parent,key,value,row,1,width=21)

    def workflow_changed(self):
        if 'family' not in self.vars:return
        # Only a fixed-voltage list changes; voltage limits never change.
        key='drain_fixed' if self.vars['workflow'].get()=='Id-Vg' else 'gate_fixed'
        self.vars['family'].set(self.vars[key].get())

    def mode_changed(self):
        real=self.vars['mode'].get()=='real'
        self.mode_badge.config(text='REAL · 실제 장비 · 매 실행 현장 검토 필수' if real else 'SIMULATION · 합성 데이터',foreground='#a15b06' if real else ACCENT)
        self.identity.config(text='미연결 · ID 확인은 버튼으로만 실행')
        self.rows=[]
        self.draw_plot()

    def settings(self):
        v={k:x.get() for k,x in self.vars.items()}
        s=defaults(self.workspace)
        s.update(mode=v['mode'],host=v['host'].strip(),port=int(v['port']),workflow=v['workflow'],
                 mapping={r:int(v[r+'_channel']) for r in ROLES},
                 limits={r:{'min_V':float(v[r+'_min']),'max_V':float(v[r+'_max']),'compliance_A':float(v[r+'_comp'])*1e-6} for r in ROLES},
                 fixed_V={r:float(v[r+'_fixed']) for r in ROLES},
                 family_V=[float(x.strip()) for x in v['family'].split(',')],
                 start_V=float(v['start']),stop_V=float(v['stop']),step_V=float(v['step']),direction=v['direction'],
                 return_sweep=bool(v['return']),hold_s=float(v['hold']),point_delay_s=float(v['delay']),
                 integration=int(v['integration']),line_Hz=int(v['line']),timeout_s=float(v['timeout']),
                 save_root=v['save_root'].strip(),sample=v['sample'],notes=v['notes'])
        for key in ('illumination','temperature'):
            s[key]={'status':v[key+'_status'],'description':v[key+'_desc']}
        return validate(s)

    def set_busy(self,busy):
        for widget,normal in self.inputs:
            widget.config(state='disabled' if busy else normal)
        for button in (self.review_button,self.probe_button):button.config(state='disabled' if busy else 'normal')
        self.stop_button.config(state='normal' if self.controller.active else 'disabled')

    def browse(self):
        path=filedialog.askdirectory(parent=self.root,initialdir=self.vars['save_root'].get(),title='측정 데이터 저장 폴더')
        if path:self.vars['save_root'].set(path)

    def review(self):
        if self.controller.active or self.probing or self.preflight_window:return
        try:s,plan=self.settings()
        except Exception as exc:
            messagebox.showerror('설정 오류',str(exc),parent=self.root);return
        self.set_busy(True)
        window=tk.Toplevel(self.root);self.preflight_window=window
        window.title('실행 전 검토 · 출력 명령은 아직 전송되지 않았습니다')
        window.geometry('810x730');window.configure(bg=PANEL);window.transient(self.root);window.grab_set()
        mode='실제 장비 · REAL' if s['mode']=='real' else '시뮬레이션 · 합성 데이터'
        ttk.Label(window,text=mode,font=('맑은 고딕',19,'bold'),foreground='#a15b06' if s['mode']=='real' else ACCENT).pack(anchor='w',padx=22,pady=16)
        details=['측정: %s   |   총 %d points   |   %d curves' % (s['workflow'],len(plan),max(p['curve'] for p in plan)),
                 '순서: 고정 전압 목록 → forward → reverse (전환점 제외)',
                 '최소 추정 %.2f s + 통신/레인지 안정화/저장 시간 (총시간 보장 아님)' % estimate(s,plan),
                 '연결 대상: '+('127.0.0.1 동적 포트 mock' if s['mode']=='simulation' else '%s:%s'%(s['host'],s['port'])),
                 '예상 ID: '+EXPECTED_ID,'']
        for role in ROLES:
            values=[p[role] for p in plan];lim=s['limits'][role]
            details.append('%s = SMU%d  |  계획 %.9g…%.9g V  |  승인 범위 %.9g…%.9g V  |  %.9g µA' % (ROLE_KO[role],s['mapping'][role],min(values),max(values),lim['min_V'],lim['max_V'],lim['compliance_A']*1e6))
        details += ['', '패밀리 목록: '+str(s['family_V']) if s['workflow']!='Spot' else 'Spot: 고정 전압 1 point',
                    '첫 점: '+str({r:plan[0][r] for r in ROLES}), '끝 점: '+str({r:plan[-1][r] for r in ROLES}),
                    'Hold %.3g s / point delay %.3g s / IT%d / %d Hz / timeout %.3g s' % (s['hold_s'],s['point_delay_s'],s['integration'],s['line_Hz'],s['timeout_s']),
                    '각 곡선 시작 전 0 V → 첫 바이어스 → hold → delay → TI(D,S,G) 순차.',
                    '각 point 바이어스/상태 확인 후 delay. 전류 읽기는 동시 측정이 아닙니다.',
                    '광: %s / %s'%(s['illumination']['status'],s['illumination']['description']),
                    '온도: %s / %s'%(s['temperature']['status'],s['temperature']['description']),
                    '저장: '+s['save_root']+'/'+('REAL' if s['mode']=='real' else 'SIMULATION'),'',
                    '검증된 실제 시험: D +0.05 V / S,G 0 V / compliance 각 100 µA.',
                    '넓은 전압과 패밀리 측정은 실제 장비에서 검증되지 않았습니다.',
                    '종료 시 zero + bare DV output-stop을 시도합니다. ACK는 실제 OFF 증거가 아닙니다.',
                    '네트워크 손실 시 출력 OFF 보장 불가 → 장비 앞에서 직접 확인해야 합니다.']
        text=tk.Text(window,bg='#ffffff',fg=INK,font=('맑은 고딕',10),wrap='word',relief='flat',padx=12,pady=10,height=23)
        text.pack(fill='both',expand=True,padx=22,pady=(0,10));text.insert('1.0','\n'.join(details));text.config(state='disabled')
        checks=[]
        for label in (['배선, KCON 번호/SMU 기능, 표시된 소자 한계와 모든 전압을 현장에서 확인했습니다.',
                       '다른 제어 프로그램은 닫았으며, 중단/통신 손실 시 장비를 직접 확인할 수 있습니다.'] if s['mode']=='real' else ['합성 데이터이며 실제 측정 결과가 아님을 확인했습니다.']):
            var=tk.BooleanVar(value=False);checks.append(var)
            ttk.Checkbutton(window,text=label,variable=var).pack(anchor='w',padx=22,pady=3)
        buttons=ttk.Frame(window,style='Card.TFrame');buttons.pack(fill='x',padx=22,pady=14)
        def dismiss():
            self.preflight_window=None;window.destroy();self.set_busy(False)
        def start():
            if not all(x.get() for x in checks):
                messagebox.showerror('확인 필요','실행 검토 항목을 확인해 주세요.',parent=window);return
            approval={'settings_hash':hashlib.sha256(json.dumps(s,sort_keys=True).encode()).hexdigest(),'confirmed_at':now(),
                      'local_review':True,'device_bounds_and_wiring_confirmed':True,'other_clients_closed':s['mode']=='real'}
            self.preflight_window=None;window.destroy()
            self.begin(s,plan,approval)
        self.preflight_start=ttk.Button(buttons,text='명시적으로 시작',style='Accent.TButton',command=start)
        self.preflight_start.pack(side='left')
        ttk.Button(buttons,text='돌아가기',command=dismiss).pack(side='left',padx=10)
        self.preflight_checks=checks
        window.protocol('WM_DELETE_WINDOW',dismiss)

    def begin(self,s,plan,approval):
        try:
            self.controller.start(s,approval)
            self.rows=[];self.total_points=len(plan);self.progress.config(value=0,maximum=len(plan))
            self.current_label.config(text='— A');self.status_label.config(text='실행 중 · 중단 버튼으로 안전 정리 요청',foreground=ACCENT)
            self.set_busy(True);self.draw_plot()
        except Exception as exc:
            self.set_busy(False);messagebox.showerror('시작 실패',str(exc),parent=self.root)

    def probe(self):
        if self.probing or self.controller.active or self.preflight_window:return
        try:
            s,_=self.settings()
        except Exception as exc:
            messagebox.showerror('설정 오류',str(exc),parent=self.root);return
        if s['mode']=='real' and not messagebox.askokcancel('수동 ID 연결',
                '%s:%d에 연결해 ID만 조회합니다. 다른 제어 프로그램을 닫고 실제 장비 주소를 확인하세요.\n출력 상태는 이 조회로 확인할 수 없습니다.'%(s['host'],s['port']),parent=self.root):return
        self.probing=True;self.set_busy(True);self.identity.config(text='ID 확인 중…')
        def worker():
            mock=client=None
            outcome='연결 실패'
            try:
                host,port=s['host'],s['port']
                if s['mode']=='simulation':mock=MockKXCI();host,port='127.0.0.1',mock.port
                client=KXCI(host,port,s['timeout_s'],s['mode']=='simulation');client.connect()
                identity=client.exchange('ID')
                outcome=identity+' · 조회 후 연결 해제'+(' · ID 불일치' if identity!=EXPECTED_ID else '')
            except Exception as exc:outcome='연결 실패: '+str(exc)
            finally:
                if client:client.close()
                if mock:mock.close()
                self.controller.events.put(('probe',outcome))
        threading.Thread(target=worker,daemon=False).start()

    def stop(self):
        self.controller.stop();self.stop_button.config(state='disabled')
        self.status_label.config(text='중단 요청됨 · 현재 명령 timeout 후 정리까지 기다립니다.',foreground='#a15b06')

    def poll(self):
        try:
            while True:
                kind,data=self.controller.events.get_nowait()
                if kind=='identity':self.identity.config(text=data+' · 실행 세션')
                elif kind=='probe':
                    self.probing=False;self.identity.config(text=data);self.set_busy(False)
                elif kind=='phase':self.status_label.config(text=data)
                elif kind=='point':
                    self.rows.append(data);self.progress.config(value=data['point_index'])
                    self.progress_label.config(text='%d / %d points  ·  Curve %d / %s  ·  %.2f s' % (data['point_index'],self.total_points,data['curve'],data['branch'],data['elapsed_s']))
                    value=data.get('drain_current_A');self.current_label.config(text='%.5g A'%value if value is not None else '— A')
                    self.draw_plot()
                elif kind=='done':
                    self.set_busy(False);self.last_path=data['path'];self.identity.config(text='연결 해제 · 다음 실행은 새 검토 필요')
                    warn='UNKNOWN' in data['cleanup']['status']
                    text={'completed':'완료','cancelled':'중단 완료','error':'오류로 중단'}[data['status']]
                    text += ' · '+('출력 상태 확인 불가! 장비 현장 확인 필요' if warn else 'zero / stop 응답만 확인 · 실제 OFF 별도 확인')
                    if data['error']:text+='\n'+data['error']
                    if data.get('save_error'):text+='\n저장 오류: '+data['save_error']
                    if data.get('recovery_path'):text+='\n복구 사본: '+data['recovery_path']
                    self.status_label.config(text=text,foreground='#a15b06' if warn or data['status']!='completed' else ACCENT)
                    self.refresh_history()
                    if warn and not self.closing:messagebox.showwarning('출력 상태 확인 불가',text,parent=self.root)
        except queue.Empty:pass
        if self.closing and not self.controller.active and not self.probing:
            if self.controller.last_result and 'UNKNOWN' in self.controller.last_result['cleanup']['status']:
                messagebox.showwarning('종료 전 현장 확인','통신 손실로 출력 OFF를 확인할 수 없습니다. 장비 앞에서 확인하세요.',parent=self.root)
            self.destroy();return
        self.poll_id = self.root.after(80,self.poll)

    def draw_plot(self):
        if not hasattr(self,'canvas'):return
        c=self.canvas;c.delete('all');w=max(c.winfo_width(),200);h=max(c.winfo_height(),100)
        left,right,top,bottom=85,w-20,42,h-48
        groups={}
        for row in self.rows:
            if row.get('drain_current_A') is not None and row.get('drain_status')=='normal':
                groups.setdefault((row['curve'],row['branch']),[]).append((row['sweep_V'],row['drain_current_A']))
        pts=[p for group in groups.values() for p in group]
        c.create_text(left,17,anchor='w',text='SIMULATION · 실제 데이터 아님' if self.vars['mode'].get()=='simulation' else 'REAL · programmed voltage / measured current',fill=ACCENT,font=('맑은 고딕',10))
        if pts:
            xs,ys=zip(*pts);xmin,xmax,ymin,ymax=min(xs),max(xs),min(ys),max(ys)
            if xmin==xmax:xmin-=.025;xmax+=.025
            if ymin==ymax:
                dy=max(abs(ymin)*.1,1e-12);ymin-=dy;ymax+=dy
        else:xmin,xmax,ymin,ymax=0,.05,0,3e-9
        ticks=4 if h>=180 else 2
        for i in range(ticks+1):
            x=left+(right-left)*i/ticks;y=bottom-(bottom-top)*i/ticks
            c.create_line(left,y,right,y,fill='#d6e3e8');c.create_line(x,top,x,bottom,fill='#e9f0f3')
            c.create_text(left-8,y,anchor='e',text='%.2e'%(ymin+(ymax-ymin)*i/ticks),fill=MUTED,font=('Consolas',9))
            c.create_text(x,bottom+16,text='%.3g'%(xmin+(xmax-xmin)*i/ticks),fill=MUTED,font=('Consolas',9))
        c.create_text((left+right)/2,h-10,text='프로그램 스윕 전압 (V)  ·  signed Id (A)  ·  점선 = reverse',fill=MUTED,font=('맑은 고딕',9))
        colors=['#087f8c','#276cbe','#a15b06','#8250b2','#c54e65']
        curves=sorted(set(key[0] for key in groups))
        for i,curve in enumerate(curves[:4]):
            c.create_text(w-220+i*52,17,text='C%d'%curve,fill=colors[(curve-1)%5],font=('Consolas',9))
        for key,group in groups.items():
            coords=[]
            for x,y in group:
                xx=left+(x-xmin)/(xmax-xmin)*(right-left);yy=bottom-(y-ymin)/(ymax-ymin)*(bottom-top)
                coords.extend((xx,yy));c.create_oval(xx-2,yy-2,xx+2,yy+2,fill=colors[(key[0]-1)%5],outline='')
            if len(coords)>=4:c.create_line(*coords,fill=colors[(key[0]-1)%5],width=2,dash=(6,4) if key[1]=='reverse' else ())
        if not pts:c.create_text((left+right)/2,(top+bottom)/2,text='측정을 시작하면 여기에 표시됩니다',fill=MUTED,font=('맑은 고딕',12))

    def refresh_history(self):
        for item in self.history.get_children():self.history.delete(item)
        root=Path(self.vars['save_root'].get())
        try:
            files=sorted(list(root.glob('SIMULATION/*/settings.json'))+list(root.glob('REAL/*/settings.json')),key=lambda x:x.parent.name,reverse=True)[:100]
            for file in files:
                try:
                    meta=json.loads(file.read_text(encoding='utf-8'))
                    state=meta['status']
                    if state in ('running','prepared') and not self.controller.active:state='미완료 · 현장 확인'
                    self.history.insert('', 'end',iid=str(file.parent),text=meta['run_id'],values=('SIM' if meta['simulation'] else 'REAL',state,meta['saved_points']))
                except (OSError,ValueError,KeyError):continue
        except OSError:pass

    def open_history(self):
        selection=self.history.selection()
        if selection and Path(selection[0]).is_dir():os.startfile(selection[0])

    def close(self):
        if self.preflight_window:
            self.preflight_window.destroy();self.preflight_window=None
        if self.controller.active or self.probing:
            self.closing=True;self.controller.stop();self.set_busy(True)
            self.status_label.config(text='종료 준비 · 부분 데이터 저장과 출력 정리를 기다리는 중…',foreground='#a15b06')
        else:self.destroy()

    def destroy(self):
        if getattr(self,'poll_id',None):
            try:self.root.after_cancel(self.poll_id)
            except tk.TclError:pass
            self.poll_id=None
        self.root.destroy()


def main():
    mutex=None
    if os.name=='nt':
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        ctypes.windll.kernel32.CreateMutexW.restype=ctypes.c_void_p
        mutex=ctypes.windll.kernel32.CreateMutexW(None,False,'Local\\KeithleyLab4200SCS_v1')
        if not mutex or ctypes.windll.kernel32.GetLastError()==183:
            if mutex:
                ctypes.windll.kernel32.CloseHandle.argtypes=[ctypes.c_void_p]
                ctypes.windll.kernel32.CloseHandle(mutex)
            ctypes.windll.user32.MessageBoxW(None,'이미 실행 중인 Keithley Lab 창을 사용하세요.','중복 실행 차단',0x30)
            return
    try:
        from .workbench import Workbench
        root=tk.Tk();Workbench(root);root.mainloop()
    finally:
        if mutex:
            ctypes.windll.kernel32.CloseHandle.argtypes=[ctypes.c_void_p]
            ctypes.windll.kernel32.CloseHandle(mutex)
