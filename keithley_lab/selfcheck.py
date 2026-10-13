"""Explicit packaged smoke test. Hard-coded simulation only, never real endpoints."""
import ctypes
import json
import os
import socket
import time
import tkinter as tk

from .engine import app_root
from .workbench import Workbench as App


def run():
    os.environ['KXCI_SIM_ONLY']='1'
    connect=socket.create_connection
    def guarded(address,*args,**kwargs):
        if address[0]!='127.0.0.1':raise RuntimeError('Self-test rejects non-loopback')
        return connect(address,*args,**kwargs)
    socket.create_connection=guarded
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
    root=tk.Tk()
    app=App(root)
    destination=app_root()/'qa'
    destination.mkdir(exist_ok=True)
    report={'mode':'simulation','outbound_guard':'127.0.0.1 only','passed':False}
    try:
        assert not app.controller.active and app.vars['mode'].get()=='simulation'
        app.vars['save_root'].set(str(destination/'packaged_data'))
        app.vars['workflow'].set('Id-Vd')
        app.vars['hold'].set('0');app.vars['delay'].set('0')
        app.vars['repeats'].set('2');app.vars['settle'].set('0')
        app.review_button.invoke()
        for var in app.preflight_checks:var.set(True)
        app.preflight_start.invoke()
        deadline=time.monotonic()+30
        while app.controller.active and time.monotonic()<deadline:
            root.update();time.sleep(.01)
        root.update()
        if app.controller.active:
            app.controller.stop();app.controller.thread.join(5)
            raise RuntimeError('Packaged smoke deadline exceeded')
        assert app.controller.last_result['status']=='completed',app.controller.last_result
        assert len(app.controller.last_result['rows_in_memory'])==12
        assert len(app.controller.last_result['runs'])==2
        means=app.controller.last_result['mean_exports']['1']
        assert means['status']=='exported' and len(means['files'])==1
        from pathlib import Path
        assert 'aggregate_mean_not_raw' in Path(means['files'][0]).read_text(encoding='utf-8')
        report.update(passed=True,gui_rendered=True,points=12,sweeps=2,compatible_mean_curves=1,result=app.controller.last_result)
    except Exception as exc:
        report['error']=repr(exc)
    finally:
        app.destroy()
        (destination/'packaged-selftest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if report['passed'] else 1
