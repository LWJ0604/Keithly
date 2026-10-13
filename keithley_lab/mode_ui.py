"""Mode-scoped Tk form. Inactive fields never participate in settings construction."""
import tkinter as tk
from tkinter import ttk,messagebox
from .engine import defaults,validate,ROLES
from .planning import parse_family,generate_family,describe_plan


class ModeInputs:
    def init_mode_inputs(self):
        self.mode_editor_ready=False;self.mode_loading=True
        panel=self.tabs.nametowidget(self.tabs.tabs()[0]).inner
        for widget in panel.winfo_children():widget.destroy()
        self.inputs=[(w,state) for w,state in self.inputs if w.winfo_exists()]
        self.mode_drafts={
            'Spot':{'drain_fixed':'.05','gate_fixed':'0'},
            'Id-Vd':{'start':'0','stop':'.05','step':'.01','direction':'ascending','return':False,'family':'0'},
            'Id-Vg':{'start':'0','stop':'.05','step':'.01','direction':'ascending','return':False,'family':'.05'}}
        self.labeled(panel,'측정 종류','workflow','Spot',0,values=['Spot','Id-Vd','Id-Vg'])
        self.preview_label=ttk.Label(panel,text='',wraplength=390,foreground='#087f8c')
        self.preview_label.grid(row=1,column=0,columnspan=3,sticky='w',pady=6)
        self.labeled(panel,'공통 source Vs (V)','source_fixed','0',2)
        self.spot_frame=ttk.Frame(panel,style='Card.TFrame');self.spot_frame.grid(row=4,column=0,columnspan=3,sticky='ew')
        self.labeled(self.spot_frame,'고정 drain Vd (V)','drain_fixed','.05',0)
        self.labeled(self.spot_frame,'고정 gate Vg (V)','gate_fixed','0',1)
        self.sweep_frame=ttk.Frame(panel,style='Card.TFrame');self.sweep_frame.grid(row=5,column=0,columnspan=3,sticky='ew')
        self.axis_title=ttk.Label(self.sweep_frame,text='',font=('맑은 고딕',11,'bold'));self.axis_title.grid(row=0,column=0,columnspan=3,sticky='w',pady=6)
        for col,(key,label,val) in enumerate([('start','시작 (V)','0'),('stop','끝 (V)','.05'),('step','부호 있는 간격 (V)','.01')]):
            ttk.Label(self.sweep_frame,text=label).grid(row=1,column=col,sticky='w');self.entry(self.sweep_frame,key,val,2,col,width=12)
        self.labeled(self.sweep_frame,'방향','direction','ascending',3,values=['ascending','descending'])
        self.vars['return']=tk.BooleanVar(value=False)
        check=ttk.Checkbutton(self.sweep_frame,text='왕복 · 전환점 중복 제외',variable=self.vars['return']);check.grid(row=4,column=0,columnspan=3,sticky='w');self.inputs.append((check,'normal'))
        self.family_frame=ttk.Frame(panel,style='Card.TFrame');self.family_frame.grid(row=6,column=0,columnspan=3,sticky='ew')
        self.family_title=ttk.Label(self.family_frame,text='',font=('맑은 고딕',11,'bold'));self.family_title.grid(row=0,column=0,columnspan=3,sticky='w',pady=(8,2))
        self.entry(self.family_frame,'family','0',1,0,width=35,columnspan=3)
        ttk.Label(self.family_frame,text='쉼표 목록 그대로 실행 · 중복 거부 · 자동 정렬 없음',style='Muted.TLabel').grid(row=2,column=0,columnspan=3,sticky='w')
        for col,(key,label) in enumerate([('family_start','목록 시작 V'),('family_stop','목록 끝 V'),('family_step','목록 간격 V')]):
            ttk.Label(self.family_frame,text=label).grid(row=3,column=col,sticky='w',pady=(8,0));self.entry(self.family_frame,key,'1' if key=='family_step' else '0',4,col,width=12)
        button=ttk.Button(self.family_frame,text='범위 생성 → 위 목록에 적용',command=self.apply_family_range)
        button.grid(row=5,column=0,columnspan=3,sticky='w',pady=5);self.inputs.append((button,'normal'))
        ttk.Label(self.family_frame,text='범위 값은 적용 버튼을 누른 뒤에만 계획에 반영됩니다.',style='Muted.TLabel').grid(row=6,column=0,columnspan=3,sticky='w')
        common=ttk.Frame(panel,style='Card.TFrame');common.grid(row=3,column=0,columnspan=3,sticky='ew',pady=8)
        ttk.Label(common,text='전체 스윕 반복').pack(side='left')
        entry=ttk.Entry(common,textvariable=self.vars['repeats'],width=7);entry.pack(side='left',padx=8);self.inputs.append((entry,'normal'))
        button=ttk.Button(common,text='측정 순서 전체 보기',command=self.show_order);button.pack(side='left');self.inputs.append((button,'normal'))
        self.active_workflow='Spot';self.mode_editor_ready=True;self.mode_loading=False
        self.vars['workflow'].trace_add('write',lambda *a:self.workflow_changed())
        for key,var in self.vars.items():
            if not key.startswith('device_'):
                var.trace_add('write',lambda *a:self.update_preview())
        self.workflow_changed()

    def workflow_changed(self):
        if not getattr(self,'mode_editor_ready',False):return super().workflow_changed()
        if self.mode_loading:return
        mode=self.vars['workflow'].get()
        previous=self.active_workflow
        keys=self.mode_drafts[previous].keys()
        self.mode_drafts[previous]={key:self.vars[key].get() for key in keys}
        self.mode_loading=True
        for key,value in self.mode_drafts[mode].items():self.vars[key].set(value)
        # Generator drafts are helpers only and never read into acquisition settings.
        self.vars['family_start'].set('0');self.vars['family_stop'].set('0');self.vars['family_step'].set('1')
        self.active_workflow=mode
        if mode=='Spot':self.spot_frame.grid();self.sweep_frame.grid_remove();self.family_frame.grid_remove()
        else:
            self.spot_frame.grid_remove();self.sweep_frame.grid();self.family_frame.grid()
            self.axis_title.config(text=('Vd' if mode=='Id-Vd' else 'Vg')+' 스윕 · 시작 → 끝')
            self.family_title.config(text=('고정 Vg' if mode=='Id-Vd' else '고정 Vd')+' family 목록 (V)')
        self.mode_loading=False;self.update_preview()

    def settings(self):
        if not getattr(self,'mode_editor_ready',False):return super().settings()
        v={k:x.get() for k,x in self.vars.items()};s=defaults(self.workspace)
        s.update(mode=v['mode'],host=v['host'].strip(),port=int(v['port']),workflow=v['workflow'],
                 mapping={r:int(v[r+'_channel']) for r in ROLES},
                 limits={r:{'min_V':float(v[r+'_min']),'max_V':float(v[r+'_max']),'compliance_A':float(v[r+'_comp'])*1e-6} for r in ROLES},
                 fixed_V={'source':float(v['source_fixed']),'drain':0.,'gate':0.},
                 hold_s=float(v['hold']),point_delay_s=float(v['delay']),integration=int(v['integration']),line_Hz=int(v['line']),timeout_s=float(v['timeout']),
                 save_root=v['save_root'].strip(),sample=v['sample'],notes=v['notes'])
        if s['workflow']=='Spot':s['fixed_V'].update(drain=float(v['drain_fixed']),gate=float(v['gate_fixed']))
        else:s.update(family_V=parse_family(v['family']),start_V=float(v['start']),stop_V=float(v['stop']),step_V=float(v['step']),direction=v['direction'],return_sweep=bool(v['return']))
        for key in ('illumination','temperature'):s[key]={'status':v[key+'_status'],'description':v[key+'_desc']}
        return validate(s)

    def apply_family_range(self):
        if self.controller.active or self.preflight_window:return
        try:
            values=generate_family(*(self.vars[k].get() for k in ('family_start','family_stop','family_step')))
            self.vars['family'].set(', '.join('%.12g'%x for x in values))
        except Exception as exc:messagebox.showerror('목록 생성 오류',str(exc),parent=self.root)

    def update_preview(self):
        if not getattr(self,'mode_editor_ready',False) or self.mode_loading:return
        try:
            s,plan=self.settings();n=int(self.vars['repeats'].get());lines=describe_plan(s,n,False)
            if s['workflow']=='Spot':summary=lines[0]+'\n'+lines[1]
            else:
                first=[p for p in plan if p['curve']==1]
                forward=sum(p['branch']=='forward' for p in first);reverse=len(first)-forward
                summary='%s · %d families · %d점/반복 × %d = %d점\n각 family: forward %d + reverse %d점 · 입력 순서 유지'%(s['workflow'],len(s['family_V']),len(plan),n,len(plan)*n,forward,reverse)
            self.preview_label.config(text=summary,foreground='#087f8c')
        except Exception as exc:self.preview_label.config(text='계획 확인 필요: '+str(exc),foreground='#a15b06')

    def show_order(self):
        try:s,_=self.settings();lines=describe_plan(s,int(self.vars['repeats'].get()))
        except Exception as exc:messagebox.showerror('계획 오류',str(exc),parent=self.root);return
        window=tk.Toplevel(self.root);window.title('현재 단계의 실제 측정 순서');window.geometry('850x650');window.transient(self.root)
        text=tk.Text(window,wrap='word',bg='white',fg='#16313d',font=('맑은 고딕',10));scroll=ttk.Scrollbar(window,command=text.yview)
        scroll.pack(side='right',fill='y');text.pack(fill='both',expand=True);text.config(yscrollcommand=scroll.set)
        text.insert('1.0','\n'.join(lines));text.config(state='disabled')

    def apply_item_settings(self,item):
        s,plan=validate(item['settings']);mode=s['workflow']
        self.vars['workflow'].set(mode);self.mode_loading=True
        values={'mode':s['mode'],'host':s['host'],'port':s['port'],'source_fixed':s['fixed_V']['source'],
                'hold':s['hold_s'],'delay':s['point_delay_s'],'integration':s['integration'],'line':s['line_Hz'],'timeout':s['timeout_s'],
                'sample':s['sample'],'notes':s['notes'],'save_root':s['save_root'],'repeats':item['repeats']}
        if mode=='Spot':values.update(drain_fixed=s['fixed_V']['drain'],gate_fixed=s['fixed_V']['gate'])
        else:values.update(start=s['start_V'],stop=s['stop_V'],step=s['step_V'],direction=s['direction'],family=', '.join(str(x) for x in s['family_V']),**{'return':s['return_sweep']})
        for role in ROLES:values.update({role+'_channel':s['mapping'][role],role+'_min':s['limits'][role]['min_V'],role+'_max':s['limits'][role]['max_V'],role+'_comp':s['limits'][role]['compliance_A']*1e6})
        for key in ('illumination','temperature'):values.update({key+'_status':s[key]['status'],key+'_desc':s[key]['description']})
        for key,value in values.items():self.vars[key].set(value)
        self.mode_drafts[mode]={key:self.vars[key].get() for key in self.mode_drafts[mode]}
        self.mode_loading=False;self.update_preview()
