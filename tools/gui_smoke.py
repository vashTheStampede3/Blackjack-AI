"""Exercise real Tk buttons in an isolated profile using synthetic screen data.

No OS input, casino controls, private desktop capture or remote inference.
Recognition quality is deliberately outside this functional smoke test.
"""
import ctypes
import json
from pathlib import Path
import tempfile
import time
import traceback
import tkinter as tk
from tkinter import messagebox,filedialog

import numpy as np

from tools.common import ROOT,write_json
from lib.common import constants,settings


def main():
    try: ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception: pass
    isolated=Path(tempfile.mkdtemp(prefix="blackjack-gui-smoke-"))
    constants.OUTPUT_DIR=isolated
    constants.CONTROLS_DIR=isolated/"controls"
    settings.SETTINGS_PATH=isolated/"settings.json"
    constants.BETTING["use_exact_edge"]=0
    constants.EXECUTOR["mode"]="off"
    constants.TRAINING["enabled"]=0
    results=[]; callback_errors=[]; messages=[]
    messagebox.askyesno=lambda *a,**k:True # isolated test counts only
    messagebox.showerror=lambda title,text,**k:messages.append({"type":"error","title":title,"text":str(text)})
    messagebox.showwarning=lambda title,text,**k:messages.append({"type":"warning","title":title,"text":str(text)})
    messagebox.showinfo=lambda title,text,**k:messages.append({"type":"info","title":title,"text":str(text)})
    filedialog.asksaveasfilename=lambda *a,**k:str(isolated/"export.csv")
    from lib.logic.log_manager import LogManager
    from lib.interfaces.modern_gui import ModernBlackjackGUI
    app=ModernBlackjackGUI(LogManager())
    app.geometry("1400x950+30+30")
    app.report_callback_exception=lambda *exc:callback_errors.append("".join(traceback.format_exception(*exc)))
    engine=app.controller.engine
    engine._warm_advice_job=lambda *a:None # avoid an unrelated full-shoe EV sweep
    def synthetic_frame():
        w,h=engine.capture.resolution
        frame=np.full((h,w,3),(45,80,45),np.uint8)
        return frame
    engine.capture.grab_bgr=synthetic_frame

    def pump(seconds=.15):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            app.update();time.sleep(.015)

    def widgets(owner):
        for child in owner.winfo_children():
            yield child
            yield from widgets(child)

    def button(owner,text):
        found=[w for w in widgets(owner) if isinstance(w,tk.Button) and text in str(w.cget("text"))]
        if not found: raise AssertionError(f"Button unavailable: {text}")
        return found[0]

    def click(text,owner=None):
        b=button(owner or app,text)
        if str(b.cget("state"))=="disabled": raise AssertionError(f"Button disabled: {text}")
        b.invoke();pump()

    def tops(): return [w for w in app.winfo_children() if isinstance(w,tk.Toplevel) and w.winfo_exists()]
    def close_tops():
        for window in tops(): window.destroy()
        pump()

    def check(name,action):
        before=len(callback_errors)
        try:
            action()
            if len(callback_errors)>before: raise AssertionError(callback_errors[-1])
            results.append({"feature":name,"passed":True})
        except Exception:
            results.append({"feature":name,"passed":False,"error":traceback.format_exc()})
            close_tops()

    def open_window(label):
        click(label)
        if not tops(): raise AssertionError(f"{label} did not open a window")
        for top in tops():
            # Every tab must render successfully, including initially hidden ones.
            from tkinter import ttk
            for widget in widgets(top):
                if isinstance(widget,ttk.Notebook):
                    for tab in widget.tabs(): widget.select(tab);pump(.04)
        close_tops()

    def choose_first():
        picker=next(w for w in tops() if "card" in w.title().lower())
        choices=[w for w in widgets(picker) if isinstance(w,tk.Button) and str(w.cget("image"))]
        assert choices,"Card picker has no card images"
        choices[0].invoke();pump(.2)

    def manual_cards():
        app.table.seats[0]["cards"][0].event_generate("<Button-1>");pump();choose_first()
        assert len(engine.seats[0].cards)==1
        app.table.seats[0]["cards"][1].event_generate("<Button-1>");pump();choose_first()
        assert len(engine.seats[0].cards)==2
        split=app.table.seats[0]["split_btn"]
        assert split is not None
        split.event_generate("<Button-1>");pump()
        assert engine.seats[0].split
        app.table.dealer_card_lbl.event_generate("<Button-1>");pump();choose_first()
        assert engine.dealer_card
        assert engine.counter.cards_seen==3

    def resets():
        before=engine.counter.cards_seen
        click("New Round")
        assert engine.counter.cards_seen==before and not engine.seats[0].cards
        click("New Shoe")
        assert engine.counter.cards_seen==0 and engine.counter.running_count==0

    def preview():
        click("Preview Regions")
        deadline=time.monotonic()+12
        while app._preview_busy and time.monotonic()<deadline:pump(.1)
        assert app.table._preview_item is not None,"Preview did not render"
        app.table._preview_close_btn.invoke();pump()
        assert app.table._preview_item is None

    def detection():
        click("Start Detection")
        deadline=time.monotonic()+15
        while app.controller.state=="starting" and time.monotonic()<deadline:pump(.1)
        assert app.controller.state=="running",engine.last_error
        pump(2)
        assert engine.last_error is None,engine.last_error
        assert not engine.provider.has_hosted()
        click("Stop Detection")
        deadline=time.monotonic()+5
        while app.controller._thread.is_alive() and time.monotonic()<deadline:pump(.1)
        assert not app.controller._thread.is_alive()

    def counters():
        plus=next(w for w in widgets(app) if isinstance(w,tk.Button) and str(w.cget("text"))=="+")
        minus=next(w for w in widgets(app) if isinstance(w,tk.Button) and str(w.cget("text")) in ("−","-"))
        plus.invoke();pump();assert engine.counter.cards_seen==1
        minus.invoke();pump();assert engine.counter.cards_seen==0

    def settings_save():
        click("Settings"); dialog=next(w for w in tops() if "Settings" in w.title())
        click("Save & Apply",dialog)
        assert settings.SETTINGS_PATH.exists()
        assert json.loads(settings.SETTINGS_PATH.read_text())["executor"]["mode"]=="off"
        close_tops()

    def stats_export():
        click("Stats"); dialog=next(w for w in tops() if "Stats" in w.title() or "Session" in w.title())
        click("Refresh",dialog);click("Export CSV",dialog)
        assert (isolated/"export.csv").exists()
        close_tops()

    def bankroll_simulation():
        click("Bankroll");dialog=next(w for w in tops() if hasattr(w,"mc_var"))
        dialog.rounds_var.set(100)
        click("Run Monte Carlo",dialog)
        assert "Ruin" in dialog.mc_var.get()
        close_tops()

    def trainer_deal():
        click("Trainer");dialog=next(w for w in tops() if hasattr(w,"cd_btn"))
        dialog.cd_btn.invoke();pump(.6)
        assert len(dialog._cd_deck)==52 and dialog._cd_started is not None
        close_tops()

    def save_calibration(label):
        click(label)
        dialog=next(w for w in tops() if hasattr(w,"_save"))
        if hasattr(dialog,"rects"):
            for i,key in enumerate(dialog.rects):
                dialog.rects[key]=[[20+i*45,30],[50+i*45,60]]
        if hasattr(dialog,"include"):
            for var in dialog.include.values():var.set(True)
        click("Save",dialog)
        assert not dialog.winfo_exists(),f"{label} did not save/close"
        assert (isolated/"region_profiles.json").exists()
        close_tops()

    def run():
        try:
            check("opens and auto-selects monitor",lambda:(_ for _ in ()).throw(AssertionError("No monitor")) if app.monitor is None else None)
            check("rank-only card images",lambda:[app.table.card_image(rank) for rank in ["Ace","2","10","Jack","Queen","King"]])
            for label in ["Logs","Stats","Leaks","Bankroll","Trainer","Settings"]:
                check(f"{label}: window and tabs",lambda label=label:open_window(label))
            check("settings save/apply in temporary profile",settings_save)
            check("stats refresh and CSV export",stats_export)
            check("bankroll Monte Carlo, bounded 100 rounds",bankroll_simulation)
            check("trainer starts a 52-card practice deck",trainer_deal)
            check("HUD on/off",lambda:(click("HUD"),click("HUD")))
            for label in ["Calibrate Regions","OCR Regions","Capture Controls","Capture Anchors"]:
                check(f"{label}: open/cancel",lambda label=label:open_window(label))
            check("manual cards, dealer and split",manual_cards)
            check("new round and new shoe",resets)
            check("manual count +/-",counters)
            check("region preview open/close",preview)
            check("local detection start/stop",detection)
            for label in ["Calibrate Regions","OCR Regions","Capture Controls","Capture Anchors"]:
                check(f"{label}: save isolated calibration",lambda label=label:save_calibration(label))
            assert app.executor.mode=="off" and not app.executor.armed
            # Screenshot contains only this app's opaque client area.
            app.lift();pump(.2)
            import mss
            from PIL import Image
            with mss.MSS() as capture:
                image=capture.grab({"left":app.winfo_rootx(),"top":app.winfo_rooty(),
                    "width":app.winfo_width(),"height":app.winfo_height()})
                Image.frombytes("RGB",(image.width,image.height),image.rgb).save(ROOT/"reports/gui_smoke.png")
        except Exception:
            callback_errors.append(traceback.format_exc())
        finally:
            close_tops()
            app.controller.stop()
            thread=app.controller._thread
            if thread and thread.is_alive():thread.join(timeout=5)
            payload={"scope":"Functional Tk button test; synthetic capture; isolated profile",
                "results":results,"callback_errors":callback_errors,"messages":messages,
                "recognition_accuracy_tested":False,"executor":"off","isolated_profile":str(isolated)}
            write_json(ROOT/"reports/gui_smoke.json",payload)
            app._on_close()
    app.after(1000,run)
    app.mainloop()
    failed=sum(not row["passed"] for row in results)
    print(json.dumps({"features":len(results),"failed":failed,"callback_errors":len(callback_errors),
        "reported_errors":sum(m["type"]=="error" for m in messages)}))
    if failed or callback_errors or any(m["type"]=="error" for m in messages): raise SystemExit(1)


if __name__=="__main__": main()
