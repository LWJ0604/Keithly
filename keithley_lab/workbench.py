"""Light desktop workbench with stable device metadata and finite reviewed queues."""
import copy
import json
from pathlib import Path
import queue
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from .gui import App, ScrollPanel, BG, PANEL, INK, MUTED, ACCENT
from .engine import ROLES, now
from .metadata import device_document, render, read_document, save_new, safe_name
from .sequence import SequenceController, review_queue, digest
from .mode_ui import ModeInputs
from .planning import describe_plan, save_queue_file, load_queue_file


class Workbench(ModeInputs,App):
    def __init__(self,root,workspace=None):
        self.queue_items=[];self.queue_review=None;self.series_rows=[];self.last_series=None
        super().__init__(root,workspace)
        self.controller=SequenceController(self.workspace)
        for child in self.tabs.nametowidget(self.tabs.tabs()[3]).inner.winfo_children():
            if isinstance(child,ttk.Label) and str(child.cget('text')).startswith('SIMULATION / REAL'):
                child.config(text='SIM: 앱 simulation_data에만 저장\nREAL: 선택 root / device / 실제 시작일\n개별 반복 분석: 원본 폴더\n평균만 분석: 평균_분석입력 폴더\n두 입력을 함께 선택하지 마세요.\n전체 CSV·명령·설정은 기록 폴더.\nOneDrive 동기화 완료는 별도 확인.\n광/온도 장비 제어 없음. unknown은 미확인 그대로.')
        self.root.title('Keithley Lab 1.2.1 · 4200-SCS · Simulation first')
        self.review_button.config(text='전체 큐 검토 → 시작')
        for i,title in enumerate(['스윕','시간','한계','조건']):self.tabs.tab(i,text=title)
        ttk.Style(root).configure('TNotebook.Tab',padding=(9,8))
        panel=ScrollPanel(self.tabs);self.tabs.add(panel,text='소자');device=panel.inner
        ttk.Label(device,text='안정적인 소자 정보',font=('맑은 고딕',12,'bold')).grid(row=0,column=0,columnspan=2,sticky='w',pady=8)
        self.device_panel=panel
        self.device_entry=self.labeled(device,'Device ID','device_id','SIM-device',1)
        ttk.Style(root).configure('Invalid.TEntry',fieldbackground='#fff0ed',foreground='#a32616')
        self.device_hint=ttk.Label(device,text='',wraplength=380,style='Muted.TLabel')
        self.vars['device_id'].trace_add('write',lambda *args:self.device_id_hint())
        self.device_id_hint()
        self.labeled(device,'형태 / pair','device_pair','custom',2,values=['custom','drain_fold','center_fold'])
        for row,(key,label) in enumerate([('L_um','L (µm, 빈칸 = unknown)'),('W_um','W (µm)'),('material','재료 (미확인 시 빈칸)'),('metal','전극 (예: Au only)'),('oxide','산화막 (예: SiO2)'),('oxide_nm','산화막 두께 (nm)')],3):
            self.labeled(device,label,'device_'+key,'',row)
        self.vars['device_confirmed']=tk.BooleanVar(value=False)
        check=ttk.Checkbutton(device,text='입력한 치수/전극/산화막을 이 소자에 대해 확인',variable=self.vars['device_confirmed'])
        check.grid(row=9,column=0,columnspan=2,sticky='w');self.inputs.append((check,'normal'))
        bar=ttk.Frame(device,style='Card.TFrame');bar.grid(row=10,column=0,columnspan=2,sticky='w',pady=8)
        for title,fn in [('확인된 소자 값 적용',self.preset),('device.md 미리보기',self.preview_device)]:
            button=ttk.Button(bar,text=title,command=fn);button.pack(side='left',padx=(0,5));self.inputs.append((button,'normal'))
        ttk.Label(device,text='프리셋은 정확한 drain-fold / center-fold 소자에만 적용합니다.\n재료 두께·이동도·유전율·온도는 채우지 않습니다.\n기존 device.md와 사용자의 본문을 덮어쓰지 않습니다.\n기존 문서가 있으면 안정 정보만 읽어 실행별로 보존합니다.\n광/온도 조건과 배선은 각 실행 설정에서 확인합니다.\n\nSIM 데이터와 소자 문서는 앱의 simulation_data에만 저장됩니다.\n조건 탭의 저장 위치는 REAL 모드에서만 적용됩니다.',wraplength=380,style='Muted.TLabel').grid(row=11,column=0,columnspan=2,sticky='w',pady=8)
        for child in device.grid_slaves():
            row=int(child.grid_info()['row'])
            if row>=2:child.grid_configure(row=row+1)
        self.device_hint.grid(row=2,column=0,columnspan=2,sticky='w',pady=8)
        panel=ScrollPanel(self.tabs);self.tabs.add(panel,text='큐');q=panel.inner
        ttk.Label(q,text='완전한 스윕 반복 · 유한 실행 큐',font=('맑은 고딕',12,'bold')).grid(row=0,column=0,columnspan=3,sticky='w',pady=8)
        self.labeled(q,'각 단계 전체 반복 (1–100)','repeats','1',1)
        self.labeled(q,'스윕 간 zero/stop 후 대기 (s)','settle','.2',2)
        self.queue_list=tk.Listbox(q,height=8,bg='white',fg=INK,selectbackground=ACCENT,selectforeground='white',exportselection=False,width=40)
        self.queue_list.grid(row=3,column=0,columnspan=3,sticky='ew',pady=8)
        bar=ttk.Frame(q,style='Card.TFrame');bar.grid(row=4,column=0,columnspan=3,sticky='w')
        for text,fn in [('현재 설정 추가',self.add_step),('불러오기',self.load_step),('선택 교체',self.replace_step)]:
            button=ttk.Button(bar,text=text,command=fn);button.pack(side='left',padx=2);self.inputs.append((button,'normal'))
        bar=ttk.Frame(q,style='Card.TFrame');bar.grid(row=5,column=0,columnspan=3,sticky='w',pady=5)
        for text,fn in [('복제',self.duplicate_step),('삭제',self.remove_step),('↑',lambda:self.move_step(-1)),('↓',lambda:self.move_step(1))]:
            button=ttk.Button(bar,text=text,command=fn);button.pack(side='left',padx=2);self.inputs.append((button,'normal'))
        diskbar=ttk.Frame(q,style='Card.TFrame');diskbar.grid(row=7,column=0,columnspan=3,sticky='w',pady=6)
        for title,fn in [('큐 파일 저장',self.save_queue),('큐 파일 불러오기',self.load_queue)]:
            button=ttk.Button(diskbar,text=title,command=fn);button.pack(side='left',padx=3);self.inputs.append((button,'normal'))
        ttk.Label(q,text='빈 큐는 현재 설정 1단계로 검토합니다.\n추가된 단계는 설정의 스냅샷입니다. 수정 후 “선택 교체”.\n모든 단계의 전압·한계·점 수를 시작 전에 검사합니다.\n중단/오류/저장 실패 시 다음 단계는 실행하지 않습니다.\n앱을 다시 열면 빈 큐, simulation 모드로 시작합니다.\n\n평균: 동일 grid/SMU/curve/branch만 정렬.\n정상 점만 평균·표본 표준편차·n_valid에 포함합니다.\n누락/오류 점은 따로 세며 보간하지 않습니다.',wraplength=380,style='Muted.TLabel').grid(row=6,column=0,columnspan=3,sticky='w',pady=8)
        self.plot_mode=tk.StringVar(value='sweep')
        options=ttk.Frame(self.canvas.master,style='Card.TFrame');options.grid(row=8,column=0,sticky='ew')
        ttk.Radiobutton(options,text='현재 스윕',value='sweep',variable=self.plot_mode,command=self.draw_plot).pack(side='left')
        ttk.Radiobutton(options,text='반복별 계획 마지막 Id (드리프트)',value='repeat',variable=self.plot_mode,command=self.draw_plot).pack(side='left',padx=8)
        self.refresh_history()
        self.init_mode_inputs()
        ttk.Button(options,text='통합 원본 폴더 열기',command=self.open_flat_exports).pack(side='left',padx=6)
        self.export_review_button=ttk.Button(self.canvas.master,text='파일별 조건 기록 / 분석 전 확인',command=self.show_export_review)
        self.export_review_button.grid(row=9,column=0,sticky='ew',pady=4)
        self.history.config(height=2)

    def show_export_review(self):
        from .export_review import completion_notice
        result=self.controller.last_result or {}
        exports=[r.get('wide_export',{}) for r in result.get('runs',[])]+list(result.get('mean_exports',{}).values())
        window=tk.Toplevel(self.root);window.title('파일별 조건 기록');window.geometry('840x560')
        text=tk.Text(window,wrap='word',font=('맑은 고딕',10),padx=14,pady=14)
        bar=ttk.Scrollbar(window,command=text.yview);bar.pack(side='right',fill='y')
        text.config(yscrollcommand=bar.set);text.pack(fill='both',expand=True)
        lines=[completion_notice(result) or '이번 세션의 완료된 내보내기가 없습니다.',
               '\n온도·hold·PC 시각은 JSON에서 확인하세요. 파일명의 delay는 PC 요청 대기 시간이며 실제 점 간격이 아닙니다.',
               '메모를 수정하거나 파일을 옮긴 뒤에는 분석기에서 조건을 다시 확인하세요. 공통 device.md에 여러 실행의 온도를 덮어쓰지 마세요.\n']
        for export in exports:
            lines.append(export.get('status',''))
            lines.extend(export.get('receipts',[]));lines.extend(export.get('held_files',[]))
        text.insert('1.0','\n'.join(lines));text.config(state='disabled')

    def open_flat_exports(self):
        import os
        selected=self.history.selection()
        path=Path(selected[0]) if selected else Path(self.controller.last_result['path']) if self.controller.last_result else None
        if path:
            target=path.parent.parent/'원본'
            if target.is_dir():os.startfile(str(target))

    def device(self):
        v={k:x.get() for k,x in self.vars.items()}
        try:safe_name(v['device_id'])
        except ValueError:
            self.tabs.select(self.device_panel)
            self.device_panel.canvas.yview_moveto(0)
            self.device_entry.focus_set()
            self.device_id_hint()
            raise
        result={'id':v['device_id'],'pair':v['device_pair'],'confirmed':bool(v['device_confirmed'])}
        for key in ('L_um','W_um','oxide_nm'):result[key]=float(v['device_'+key]) if v['device_'+key].strip() else None
        for key in ('material','metal','oxide'):result[key]=v['device_'+key].strip()
        device_document(result);return result

    def device_id_hint(self):
        try:
            safe_name(self.vars['device_id'].get())
            text='Device ID: 1~80자 · 앞뒤 공백 금지 · 실제 모드 전환 시 SIM-device는 비워집니다.'
            style='TEntry';color=MUTED
        except ValueError as exc:
            text=str(exc);style='Invalid.TEntry';color='#a32616'
        self.device_entry.config(style=style)
        self.device_hint.config(text=text,foreground=color)

    def mode_changed(self):
        super().mode_changed()
        if self.vars['mode'].get()=='real' and 'device_id' in self.vars and self.vars['device_id'].get()=='SIM-device':
            self.vars['device_id'].set('')

    def preset(self):
        pair=self.vars['device_pair'].get();expected={'drain_fold':'drain-fold','center_fold':'center-fold'}.get(pair)
        if not expected or self.vars['device_id'].get()!=expected:
            messagebox.showerror('소자 확인','Device ID를 정확히 drain-fold 또는 center-fold로 입력하고 해당 pair를 선택하세요.',parent=self.root);return
        self.vars['device_L_um'].set('1.5' if pair=='drain_fold' else '2')
        self.vars['device_W_um'].set('0.4');self.vars['device_oxide'].set('SiO2');self.vars['device_oxide_nm'].set('90');self.vars['device_metal'].set('Au only')
        self.vars['device_confirmed'].set(False)

    def preview_device(self):
        try:
            data=device_document(self.device());base=self.workspace/'simulation_data' if self.vars['mode'].get()=='simulation' else Path(self.vars['save_root'].get())
            path=base/safe_name(self.vars['device_id'].get())/'device.md'
            exists=path.exists();existing=read_document(path)[2] if exists else ''
        except Exception as exc:messagebox.showerror('메타데이터 오류',str(exc),parent=self.root);return
        window=tk.Toplevel(self.root);window.title('device.md 미리보기 · 기존 파일 보존');window.geometry('850x700');window.transient(self.root);window.grab_set()
        ttk.Label(window,text=str(path),wraplength=800).pack(anchor='w',padx=16,pady=10)
        text=tk.Text(window,wrap='word',bg='white',fg=INK,font=('Consolas',10));text.pack(fill='both',expand=True,padx=16)
        text.insert('1.0',('EXISTING — PRESERVED. Current GUI proposal is shown below for comparison.\n\n'+existing+'\n\n--- PROPOSAL (NOT WRITTEN) ---\n\n' if exists else '')+render(data));text.config(state='disabled')
        def save():
            try:save_new(path.parent,data)
            except Exception as exc:messagebox.showerror('보존 / 저장 실패',str(exc),parent=window);return
            window.destroy()
        if not exists:ttk.Button(window,text='표시된 새 device.md 저장',command=save).pack(pady=8)
        else:ttk.Label(window,text='기존 문서는 읽기 전용으로 보존합니다. 실행에는 기존 안정 정보가 우선합니다.',foreground=ACCENT).pack(pady=8)
        ttk.Button(window,text='닫기',command=window.destroy).pack(pady=8)

    def selected(self):
        selection=self.queue_list.curselection()
        if not selection:raise ValueError('큐에서 단계를 선택하세요.')
        return selection[0]

    def current_item(self):
        s,_=self.settings();repeats=int(self.vars['repeats'].get())
        if not 1<=repeats<=100:raise ValueError('Repeat count must be 1..100')
        describe_plan(s,repeats,False)
        return {'settings':s,'repeats':repeats}

    def edit_queue(self,fn):
        if self.controller.active or self.preflight_window:return
        try:fn();self.render_queue()
        except Exception as exc:messagebox.showerror('큐 설정',str(exc),parent=self.root)

    def add_step(self):self.edit_queue(lambda:self.queue_items.append(self.current_item()))
    def replace_step(self):self.edit_queue(lambda:self.queue_items.__setitem__(self.selected(),self.current_item()))
    def duplicate_step(self):self.edit_queue(lambda:self.queue_items.insert(self.selected()+1,copy.deepcopy(self.queue_items[self.selected()])) )
    def remove_step(self):self.edit_queue(lambda:self.queue_items.pop(self.selected()))
    def move_step(self,delta):
        def move():
            i=self.selected();j=i+delta
            if 0<=j<len(self.queue_items):self.queue_items[i],self.queue_items[j]=self.queue_items[j],self.queue_items[i]
        self.edit_queue(move)

    def load_step(self):
        if self.controller.active or self.preflight_window:return
        try:
            self.apply_item_settings(self.queue_items[self.selected()])
        except Exception as exc:messagebox.showerror('큐 설정',str(exc),parent=self.root)

    def render_queue(self):
        self.queue_list.delete(0,'end')
        for i,item in enumerate(self.queue_items,1):
            s=item['settings'];self.queue_list.insert('end','%02d  %s × %d  [%s]  family %s'%(i,s['workflow'],item['repeats'],s['mode'],s['family_V']))

    def save_queue(self):
        if self.controller.active or self.preflight_window:return
        try:
            items=self.queue_items or [self.current_item()]
            path=filedialog.asksaveasfilename(parent=self.root,initialdir=str(self.workspace),defaultextension='.json',initialfile='measurement_queue.json',filetypes=[('Queue JSON','*.json')],title='새 큐 파일 저장 · 기존 파일 덮어쓰기 없음')
            if path:save_queue_file(path,items)
        except Exception as exc:messagebox.showerror('큐 저장 실패',str(exc),parent=self.root)

    def load_queue(self):
        if self.controller.active or self.preflight_window:return
        try:
            path=filedialog.askopenfilename(parent=self.root,initialdir=str(self.workspace),filetypes=[('Queue JSON','*.json')],title='큐 초안 불러오기 · 자동 실행 없음')
            if not path:return
            items=load_queue_file(path,self.vars['mode'].get())
            self.queue_items=items;self.render_queue();self.queue_list.selection_set(0);self.load_step()
            self.status_label.config(text='큐 초안 불러옴 · 현재 연결 모드 유지 · 전체 검토/명시적 시작 필요',foreground=ACCENT)
        except Exception as exc:messagebox.showerror('큐 불러오기 실패',str(exc),parent=self.root)

    def review(self):
        if self.controller.active or self.probing or self.preflight_window:return
        try:
            items=self.queue_items or [self.current_item()]
            reviewed=review_queue(items,self.device(),self.vars['save_root'].get(),self.workspace,float(self.vars['settle'].get()))
            if reviewed['mode']!=self.vars['mode'].get():raise ValueError('현재 표시 모드와 저장된 큐 모드가 다릅니다. 큐를 수정하세요.')
        except Exception as exc:messagebox.showerror('전체 큐 검토 실패',str(exc),parent=self.root);return
        self.queue_review=reviewed;self.set_busy(True)
        window=tk.Toplevel(self.root);self.preflight_window=window;window.title('전체 큐 사전 검토 · 출력 명령 전송 전');window.geometry('890x760');window.configure(bg=PANEL);window.transient(self.root);window.grab_set()
        ttk.Label(window,text='%s · %d sweeps / %d points'%(reviewed['mode'].upper(),reviewed['sweep_count'],reviewed['point_count']),font=('맑은 고딕',18,'bold'),foreground=ACCENT).pack(anchor='w',padx=20,pady=14)
        lines=['Device: '+reviewed['device']['id'],'Root: '+reviewed['root'],
               '최소 시간 %.2f s + 통신/저장/안정화; 예상 저장 약 %.1f MB (보장 아님)'%(reviewed['minimum_seconds'],reviewed['estimated_storage_bytes']/1e6),
               '스윕 간 zero/stop 응답 후 %.3g s 대기. ACK는 실제 OFF 증거가 아닙니다.'%reviewed['settle_s'],
               '기존 device.md 우선 / SHA256: '+str(reviewed['device_receipt']['sha256']),
               '저장 날짜: 각 스윕의 실제 PC 로컬 시작일. 자정 이후 다음 스윕은 새 날짜.',
               'SIM은 앱 simulation_data에만 저장. REAL은 현장 검토와 명시적 시작 필수.','']
        lines+=['분석 입력 분리: 개별 반복은 원본 / 파생 평균은 평균_분석입력. Spot 평균 곡선 export 없음.','']
        for item in reviewed['items']:
            s=item['settings'];p=item['plan'];lines.append('STEP %d: %s × %d · %d points/sweep · family %s · %s%s'%(item['step'],s['workflow'],item['repeats'],len(p),s['family_V'],s['direction'],' + reverse' if s['return_sweep'] else ''))
            for r in ROLES:
                lim=s['limits'][r];values=[x[r] for x in p]
                lines.append('  %s SMU%d: planned %g..%g V / approved %g..%g V / %.6g µA'%(r,s['mapping'][r],min(values),max(values),lim['min_V'],lim['max_V'],lim['compliance_A']*1e6))
            lines.append('  hold %g s · delay %g s · IT%d · %d Hz · timeout %g s'%(s['hold_s'],s['point_delay_s'],s['integration'],s['line_Hz'],s['timeout_s']))
            lines.append('  endpoint %s · light %s · temperature %s'%('loopback mock' if s['mode']=='simulation' else '%s:%d'%(s['host'],s['port']),s['illumination'],s['temperature']))
            lines.extend(describe_plan(s,item['repeats'],True))
        lines+=['','실제 검증: D +0.05 V, S/BG 0 V, 각 100 µA뿐입니다. 넓은 전압은 검증되지 않았습니다.',
                '오류/중단/저장 실패 → 다음 단계 중지. 연결 손실 시 출력 OFF 보장 불가.',
                '매 실행 신규 검토. 재시작/재연결 후 자동 재개 없음. 다른 제어 클라이언트는 닫아야 합니다.']
        lines+=['','실행에 보존할 안정 소자 정보 (기존 device.md 우선):',json.dumps(reviewed['stable_metadata'],ensure_ascii=False,indent=2)]
        box=ttk.Frame(window,style='Card.TFrame');box.pack(fill='both',expand=True,padx=20)
        text=tk.Text(box,wrap='word',font=('맑은 고딕',10),bg='white',fg=INK,padx=12,pady=10)
        scrollbar=ttk.Scrollbar(box,command=text.yview);scrollbar.pack(side='right',fill='y');text.config(yscrollcommand=scrollbar.set)
        text.pack(side='left',fill='both',expand=True);text.insert('1.0','\n'.join(lines));text.config(state='disabled')
        self.preflight_checks=[]
        labels=['합성 데이터이며 실제 측정이 아님을 확인했습니다.'] if reviewed['mode']=='simulation' else ['모든 단계의 소자 한계·전압·배선·KCON을 현장에서 확인했습니다.','다른 제어 클라이언트를 닫았고, 통신 손실 시 장비를 직접 확인할 수 있습니다.']
        for label in labels:
            var=tk.BooleanVar(value=False);self.preflight_checks.append(var);ttk.Checkbutton(window,text=label,variable=var).pack(anchor='w',padx=20,pady=4)
        def dismiss():self.preflight_window=None;window.destroy();self.set_busy(False)
        def start():
            if not all(v.get() for v in self.preflight_checks):messagebox.showerror('확인 필요','검토 항목을 확인해 주세요.',parent=window);return
            approval={'queue_hash':digest(reviewed),'confirmed_at':now(),'local_review':True}
            dismiss()
            try:
                self.controller.start(reviewed,approval);self.rows=[];self.series_rows=[];self.last_series=None
                self.total_points=reviewed['point_count'];self.progress.config(value=0,maximum=self.total_points);self.set_busy(True);self.draw_plot()
            except Exception as exc:messagebox.showerror('시작 실패',str(exc),parent=self.root)
        buttons=ttk.Frame(window,style='Card.TFrame');buttons.pack(fill='x',padx=20,pady=14)
        self.preflight_start=ttk.Button(buttons,text='전체 큐 명시적으로 시작',style='Accent.TButton',command=start);self.preflight_start.pack(side='left')
        ttk.Button(buttons,text='돌아가기',command=dismiss).pack(side='left',padx=10);window.protocol('WM_DELETE_WINDOW',dismiss)

    def poll(self):
        # Reuse base handling, transforming sequence point events to its stable UI contract.
        saved=[]
        try:
            while True:
                kind,data=self.controller.events.get_nowait()
                if kind=='point' and 'repeat' in data:
                    key=(data['sequence_step'],data['repeat'])
                    if key!=self.last_series:self.rows=[];self.last_series=key
                    self.series_rows.append(dict(data))
                    data=dict(data);data['point_index']=data['queue_point']
                saved.append((kind,data))
        except queue.Empty:pass
        for event in saved:self.controller.events.put(event)
        super().poll()
        if not self.closing and not self.controller.active and self.controller.last_result:
            from .export_review import completion_notice
            notice=completion_notice(self.controller.last_result)
            current=self.status_label.cget('text')
            short=notice.split('\n')[0]+'\n아래 “파일별 조건 기록”에서 JSON 경로와 상위 메모 조건을 확인하세요.'
            if notice and short not in current:
                self.status_label.config(text=current+'\n'+short,foreground='#a15b06')
        if not self.closing and not self.controller.active and self.controller.last_result and self.controller.last_result.get('warnings'):
            current=self.status_label.cget('text')
            if '드리프트 경고' not in current:
                self.status_label.config(text=current+'\n반복 드리프트 경고: 통계 JSON과 반복 그래프를 확인하세요.',foreground='#a15b06')

    def draw_plot(self):
        if not hasattr(self,'plot_mode') or self.plot_mode.get()=='sweep':
            if self.series_rows and self.last_series:
                self.rows=[r for r in self.series_rows if (r['sequence_step'],r['repeat'])==self.last_series]
            return super().draw_plot()
        c=self.canvas;c.delete('all');w=max(c.winfo_width(),200);h=max(c.winfo_height(),100)
        latest={}
        for row in self.series_rows:
            final_index=len(self.queue_review['items'][row['sequence_step']-1]['plan']) if self.queue_review else row['point_index']
            if row['point_status']=='normal' and row['point_index']==final_index:latest[(row['sequence_step'],row['repeat'])]=row
        groups={}
        for (step,repeat),row in latest.items():groups.setdefault(step,[]).append((repeat,row['drain_current_A']))
        points=[x for group in groups.values() for x in group]
        c.create_text(15,18,anchor='w',text='반복별 계획 마지막 점 (정상만) · 전체 grid 통계는 JSON',fill=ACCENT,font=('맑은 고딕',10))
        if not points:return
        xmin,xmax=1,max(x for x,y in points);ys=[y for x,y in points];lo,hi=min(ys),max(ys)
        if lo==hi:lo-=max(abs(lo)*.1,1e-12);hi+=max(abs(hi)*.1,1e-12)
        for i in range(5):
            yy=h-40-(h-90)*i/4;c.create_line(85,yy,w-20,yy,fill='#d6e3e8');c.create_text(80,yy,anchor='e',text='%.2e'%(lo+(hi-lo)*i/4),fill=MUTED)
        for step,group in groups.items():
            coords=[]
            for repeat,value in group:
                x=85+(repeat-1)/max(1,xmax-1)*(w-110);y=h-40-(value-lo)/(hi-lo)*(h-90);coords.extend((x,y));c.create_oval(x-3,y-3,x+3,y+3,fill=ACCENT,outline='')
            if len(coords)>2:c.create_line(*coords,fill=ACCENT,width=2)
        c.create_text(w/2,h-12,text='repeat index 1 … %d · Id (A)'%xmax,fill=MUTED)

    def refresh_history(self):
        if not hasattr(self,'history'):return
        for item in self.history.get_children():self.history.delete(item)
        roots=[self.workspace/'simulation_data']
        if 'save_root' in self.vars and self.vars['mode'].get()=='real':roots.append(Path(self.vars['save_root'].get()))
        try:
            files=sorted([f for root in roots for f in root.glob('*/*/기록/*/sequence.json')],key=lambda p:p.parent.name,reverse=True)[:100]
            for file in files:
                try:
                    meta=json.loads(file.read_text(encoding='utf-8'));state=meta['status']
                    if state in ('prepared','running') and not self.controller.active:state='미완료 · 자동 재개 없음'
                    self.history.insert('','end',iid=str(file.parent),text=meta['sequence_id'],values=(meta['review']['mode'],state,sum(x.get('point_count',0) for x in meta['runs'])))
                except (OSError,ValueError,KeyError):pass
        except OSError:pass
