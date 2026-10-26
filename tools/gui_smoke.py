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
from tkinter import messagebox,filedialog,ttk
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np

from tools.common import write_json
from lib.common import constants,settings
from lib.logic.app_modes import INDIVIDUAL
from lib.logic.card_layout import CardLayout
from lib.logic.table_inference import prepare_layout_rois
from lib.logic import region_profiles
import screeninfo


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
    constants.EXTRA_CARD_CONFIRM_CYCLES=1
    monitors=screeninfo.get_monitors()
    monitor=next((m for m in monitors if getattr(m,"is_primary",False)),monitors[0])
    width,height=monitor.width,monitor.height
    layout=CardLayout.from_payload({
        "mode":INDIVIDUAL,"resolution":[width,height],
        "mine":[[width*.1,height*.52],[width*.72,height*.52],
                [width*.72,height*.94],[width*.1,height*.94]],
        "dealer":[[width*.4,height*.05],[width*.75,height*.05],
                  [width*.75,height*.33],[width*.4,height*.33]],
    },(width,height))
    from lib.logic.window_capture import source_descriptor
    calibration_source=source_descriptor(monitor,"Monitor")
    region_profiles.set_card_layout(layout,profile=region_profiles.active_name(),
                                    source_context=calibration_source)
    region_profiles.set_source_context((width,height),calibration_source,
                                       profile=region_profiles.active_name())
    rois=prepare_layout_rois(np.zeros((height,width,3),np.uint8),layout)
    predictions=[]
    class SyntheticModel:
        hosted=False
        def predict(self,_frame,*_args):
            bounds=next((bounds for role,bounds,crop in rois if crop.shape==_frame.shape),None)
            if bounds is None: return []
            l,t,r,b=bounds
            return [dict(p,cx=p["cx"]-l,cy=p["cy"]-t) for p in predictions
                    if l<=p["cx"]<r and t<=p["cy"]<b]
    model=SyntheticModel()
    class SyntheticProvider:
        backend_name="Deterministic synthetic fixture"
        def players_model(self): return model
        def dealer_model(self): return model
        def has_hosted(self): return False
        def loaded_model_metadata(self,*_args): return {}
        def refresh(self,**_kwargs): return False
    from lib.logic.models import ModelProvider
    original_provider_get=ModelProvider.get
    ModelProvider.get=staticmethod(lambda:SyntheticProvider())
    results=[]; callback_errors=[]; messages=[]
    messagebox.askyesno=lambda *a,**k:True # isolated test counts only
    messagebox.showerror=lambda title,text,**k:messages.append({"type":"error","title":title,"text":str(text)})
    messagebox.showwarning=lambda title,text,**k:messages.append({"type":"warning","title":title,"text":str(text)})
    messagebox.showinfo=lambda title,text,**k:messages.append({"type":"info","title":title,"text":str(text)})
    filedialog.asksaveasfilename=lambda *a,**k:str(isolated/"export.csv")
    from lib.logic.log_manager import LogManager
    from lib.interfaces.modern_gui import ModernBlackjackGUI
    try:
        app=ModernBlackjackGUI(LogManager())
    except Exception:
        ModelProvider.get=original_provider_get
        raise
    app.geometry("1400x950+30+30")
    app.report_callback_exception=lambda *exc:callback_errors.append("".join(traceback.format_exception(*exc)))
    engine=app.controller.engine
    engine._warm_advice_job=lambda *a:None # avoid an unrelated full-shoe EV sweep
    mine_patch=next(bounds for role,bounds,_ in rois if role=="mine")
    dealer_patch=next(bounds for role,bounds,_ in rois if role=="dealer")
    mx,my,mr,mb=mine_patch
    dx,dy,dr,db=dealer_patch
    predictions.extend([
        {"class":"6","cx":mx+(mr-mx)*.46,"cy":my+(mb-my)*.52,
         "width":100,"height":150,"confidence":.99},
        {"class":"10","cx":mx+(mr-mx)*.54,"cy":my+(mb-my)*.52,
         "width":100,"height":150,"confidence":.99},
        {"class":"A","cx":dx+(dr-dx)*.5,"cy":dy+(db-dy)*.5,
         "width":100,"height":150,"confidence":.99},
    ])
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
        found=[w for w in widgets(owner) if isinstance(w,(tk.Button,ttk.Button)) and text in str(w.cget("text"))]
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
            if app.controller.running:
                try:
                    app.controller.stop()
                    deadline=time.monotonic()+5
                    while app.controller._thread and app.controller._thread.is_alive() and time.monotonic()<deadline:
                        pump(.1)
                except Exception:
                    pass

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

    def window_source_workflow():
        from unittest.mock import patch
        from lib.logic.window_capture import WindowSource
        source = WindowSource(0, "Test window", 123, 640, 480)
        with (patch("lib.logic.window_capture.list_windows", return_value=[source]),
              patch.object(WindowSource, "ready_source", return_value=source)):
            app.source_combo.set("Window")
            app.source_combo.event_generate("<<ComboboxSelected>>")
            pump()
            assert app.monitor is None and str(app.start_btn.cget("state")) == "disabled"
            app.monitor_combo.current(0)
            app.monitor_combo.event_generate("<<ComboboxSelected>>")
            pump()
            assert app.monitor is source
            assert str(app.start_btn.cget("state")) == "disabled"
            assert ("calibr" in app.monitor_status.cget("text").casefold()
                    or "calibr" in app.status_var.get().casefold())
            assert str(app.ocr_btn.cget("state")) == "disabled"
            assert "calibr" in app.status_var.get().casefold() or "calibr" in app.monitor_status.cget("text").casefold()
            click("Refresh windows")
            assert app.monitor is None
            with patch("lib.logic.window_capture.list_windows", return_value=[]):
                click("Refresh windows")
                assert len(app.monitor_combo["values"]) == 0
        app.source_combo.set("Monitor")
        app.source_combo.event_generate("<<ComboboxSelected>>")
        pump()
        app._auto_select_monitor()
        assert app.monitor is not None

    def wait_registered_cards(timeout=12):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            pump(.1)
            cards=list(engine.card_registry.cards.values())
            if len([card for card in cards if card.status=="active" and card.counted])>=3:
                return
        raise AssertionError(f"Synthetic cards were not confirmed: {engine.card_registry.snapshot()}")

    def manual_cards():
        click("Start Detection")
        deadline=time.monotonic()+12
        while app.controller.state!="running" and time.monotonic()<deadline:pump(.1)
        assert app.controller.state=="running",engine.last_error
        wait_registered_cards()
        deadline=time.monotonic()+5
        while (getattr(app,"_table_presentation",{}).get("status")!="ready"
               and time.monotonic()<deadline):
            pump(.1)
        presentation=app._table_presentation
        assert presentation["status"]=="ready",presentation
        assert app.round_overlay._last_state==presentation
        table_ids=[c.get("card_id") for player in presentation["players"] for c in player["cards"]]
        mini_ids=[c.get("card_id") for player in app.round_overlay._last_state["players"] for c in player["cards"]]
        assert table_ids==mini_ids and len(table_ids)==2
        assert len(presentation["dealer"]["cards"])==1
        assert app.table.telemetry_label.cget("text") and app.table.advice_label.cget("text")

        hero=next(player for player in app.table.seats if player["is_hero"])
        original=hero["cards"][0].card_identity
        corrected_rank="6" if original.get("rank")!="6" else "10"
        before=engine.counter.running_count
        hero["cards"][0].event_generate("<Button-1>", x=4, y=4);pump()
        picker=getattr(app,"_card_correction_picker",None)
        live=app._current_display_snapshot()
        diagnostics={
            "round":original.get("round_id")==presentation.get("round_id"),
            "shoe":original.get("shoe_id")==presentation.get("shoe_id"),
            "metadata":{k:original.get(k)==presentation.get(k) for k in ("profile_name","mode","resolution","source_id","source_ref")},
            "seq":None if live is None else (live.get("seq"),presentation.get("seq")),
            "source_context":(original.get("source_ref") or {}).get("source_context")==((live or {}).get("source_context")),
            "mode_var":app.mode_var.get(),"profile_var":app.profile_var.get(),
            "applied":app._applied_mode_context,
        }
        assert picker is not None and picker._card_identity["card_id"]==original["card_id"], (
            f"Identity picker did not open: status={app.status_var.get()!r}; "
            f"identity={original!r}; current={app._identity_is_current(original)}; "
            f"diagnostics={diagnostics!r}; configured={app._source_configured}")
        picker._set_rank(corrected_rank)
        source_prediction=next((item for item in predictions
                                if item["class"]==original.get("rank")),None)
        if source_prediction is not None:
            source_prediction["class"]=corrected_rank
        accepted=app._finish_identity_correction(
            "set_rank",corrected_rank,None,original)
        assert accepted, f"Identity correction rejected: {app.status_var.get()!r}"
        immediate_rank=engine.card_registry.cards[original["card_id"]].rank
        picker._cancel();pump(.3)
        assert immediate_rank==corrected_rank and engine.card_registry.cards[original["card_id"]].rank==corrected_rank, (
            f"Correction failed: status={app.status_var.get()!r}, "
            f"actual={engine.card_registry.cards[original['card_id']].rank!r}, "
            f"immediate={immediate_rank!r}, wanted={corrected_rank!r}, chosen={picker._chosen_rank!r}, "
            f"picker_current={picker._current_rank!r}")
        assert engine.counter.running_count!=before
        corrected=app._table_presentation
        corrected_card=next(card for player in corrected["players"] for card in player["cards"]
                            if card["card_id"]==original["card_id"])
        assert corrected_card["rank"]==corrected_rank
        assert corrected==app.round_overlay._last_state

        hero=next(player for player in app.table.seats if player["is_hero"])
        target=next(widget for widget in hero["cards"] if widget.card_identity["card_id"]!=original["card_id"])
        doomed=target.card_identity["card_id"]
        before_seen=engine.counter.cards_seen
        mini = app.round_overlay
        visual = next(item for item in mini._drawn_cards if item["identity"].get("card_id") == doomed)
        x1, y1, x2, y2 = mini.canvas.coords(visual["item"])
        x, y = round((x1+x2)/2), round((y1+y2)/2)
        mini.canvas.event_generate("<Motion>", x=x, y=y)
        mini.canvas.event_generate("<ButtonPress-1>", x=x, y=y)
        mini.canvas.event_generate("<ButtonRelease-1>", x=x, y=y)
        pump()
        picker=app._card_correction_picker
        assert picker is not None and picker._card_identity["card_id"]==doomed
        assert app._finish_identity_correction("not_a_card",None,None,target.card_identity)
        picker._cancel();pump(.3)
        assert (engine.card_registry.cards[doomed].state=="rejected"
                and engine.card_registry.cards[doomed].status=="not_a_card")
        assert engine.counter.cards_seen==before_seen-1
        assert all(card.get("card_id")!=doomed for player in app._table_presentation["players"] for card in player["cards"])
        assert app._table_presentation==app.round_overlay._last_state
        click("Stop Detection")
        deadline=time.monotonic()+5
        while app.controller._thread and app.controller._thread.is_alive() and time.monotonic()<deadline:pump(.1)
        assert not app.controller._thread or not app.controller._thread.is_alive()

    def registry_history_workflow():
        app.registry_btn.invoke();pump()
        window=app._registry_window
        assert window.winfo_exists()
        row=next(card for card in engine.get_registry_inspection()["cards"]
                 if card["status"]=="active" and card["counted"] and card["owner"]=="mine")
        cid=row["id"]
        window.tree.selection_set(cid)
        click("Corregir seleccionada",window)
        picker=app._card_correction_picker
        assert picker.attributes("-topmost")
        picker._set_rank("8");picker._save_button.invoke();pump()
        assert engine.card_registry.cards[cid].rank=="8"
        click("New Round")
        window.refresh(engine.get_registry_inspection())
        assert cid in engine.card_registry.archive
        before=engine.counter.running_count;seen=engine.counter.cards_seen
        window.tree.selection_set(cid);click("Corregir seleccionada",window)
        picker=app._card_correction_picker
        picker._set_rank("Ace");picker._save_button.invoke();pump()
        assert engine.counter.running_count==before-1 and engine.counter.cards_seen==seen
        assert engine.card_registry.archive[cid].rank=="Ace"
        assert app._table_presentation==app.round_overlay._last_state
        window.destroy()

    def omitted_card_workflow():
        click("Start Detection")
        wait_registered_cards()
        app.registry_btn.invoke();pump()
        window=app._registry_window
        before=engine.counter.cards_seen;rc=engine.counter.running_count
        click("Anadir carta omitida",window)
        location=app._manual_location_picker
        assert location is not None and location.winfo_exists()
        x=mx+(mr-mx)*.22;y=my+(mb-my)*.52
        scale=location.scale
        location.canvas.event_generate("<ButtonPress-1>",x=round((x-18)*scale),y=round((y-28)*scale))
        location.canvas.event_generate("<B1-Motion>",x=round((x+18)*scale),y=round((y+28)*scale))
        location.canvas.event_generate("<ButtonRelease-1>",x=round((x+18)*scale),y=round((y+28)*scale))
        pump()
        picker=app._card_correction_picker
        assert picker is not None and picker.attributes("-topmost")
        picker._set_rank("5");picker._save_button.invoke();pump(.4)
        added=[c for c in engine.card_registry.cards.values() if c.manual_added]
        assert len(added)==1 and added[0].rank=="5"
        assert engine.counter.cards_seen==before+1 and engine.counter.running_count==rc+1
        assert app._table_presentation==app.round_overlay._last_state
        click("Stop Detection")
        deadline=time.monotonic()+5
        while app.controller._thread and app.controller._thread.is_alive() and time.monotonic()<deadline:pump(.1)
        window.destroy()

    def resets():
        before=engine.counter.cards_seen
        click("New Round")
        assert engine.counter.cards_seen==before and not [c for c in engine.card_registry.cards.values() if c.status=="active"]
        click("New Shoe")
        assert engine.counter.cards_seen==0 and engine.counter.running_count==0

    def preview():
        click("Preview Regions")
        deadline=time.monotonic()+12
        while app._preview_busy and time.monotonic()<deadline:pump(.1)
        assert app.table._preview_item is not None,f"Preview did not render: {app.status_var.get()}"
        close_button=next(w for w in app.table._preview_close_btn.winfo_children()
                          if isinstance(w,tk.Button) and "Close preview" in str(w.cget("text")))
        close_button.invoke();pump()
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
        before=engine.counter.cards_seen
        plus=next(w for w in widgets(app) if isinstance(w,tk.Button) and str(w.cget("text"))=="+")
        minus=next(w for w in widgets(app) if isinstance(w,tk.Button) and str(w.cget("text")) in ("−","-"))
        plus.invoke();pump();assert engine.counter.cards_seen==before+1
        minus.invoke();pump();assert engine.counter.cards_seen==before

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

    def compact_pending_hands():
        from copy import deepcopy
        from lib.interfaces.table_view import TableView
        from lib.interfaces import scaling
        pending={"mode":"individual","status":"uncertain","message":"Lectura dudosa",
            "dealer":{"cards":[{"card_id":"smoke-dealer","rank":None,"state":"pending"}]},
            "players":[{"id":"hero","label":"Yo","is_hero":True,"cards":[
                {"card_id":"smoke-hero","rank":None,"state":"pending","hand":0}]}]}
        top=tk.Toplevel(app)
        view=TableView(top)
        view.canvas.pack(fill=tk.BOTH,expand=True)
        app.round_enabled.set(True)
        app._toggle_round_overlay()
        mini=app.round_overlay
        old_state=deepcopy(mini._last_state)
        try:
            for height in (220,280,400):
                top.geometry(f"800x{height}+30+30")
                top.update()
                view.update(pending)
                view._relayout()
                top.update_idletasks()
                for hand in (view.dealer,view.seats[0]):
                    for widget in (hand["name"],hand["cards"][0]):
                        assert widget.winfo_ismapped()
                        assert 0<=widget.winfo_y()
                        assert widget.winfo_y()+widget.winfo_height()<=view.canvas.winfo_height()
                    assert hand["cards"][0].card_identity["card_id"].startswith("smoke-")
            mini.update_from_presentation(pending,running=True)
            texts=[mini.canvas.itemcget(item,"text") for item in mini.canvas.find_all()
                   if mini.canvas.type(item)=="text"]
            assert "Yo" in texts and "Crupier" in texts and texts.count("?")==2
            assert len(mini._drawn_cards)==2
        finally:
            mini.update_from_presentation(old_state,running=app.controller.running)
            if view._resize_job is not None:
                view.canvas.after_cancel(view._resize_job)
            scaling.off_change(view._rescale)
            top.destroy()

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
            check("Mini Mesa on/off",lambda:(click("Mini Mesa"),click("Mini Mesa")))
            for label in ["Calibrate Regions","OCR Regions","Capture Controls","Capture Anchors"]:
                check(f"{label}: open/cancel",lambda label=label:open_window(label))
            check("physical detections and identity corrections",manual_cards)
            check("registry inspector active and historical correction",registry_history_workflow)
            check("manual omitted card from native location",omitted_card_workflow)
            check("new round and new shoe",resets)
            check("manual count +/-",counters)
            check("region preview open/close",preview)
            check("local detection start/stop",detection)
            check("window selection, OCR client coordinates, refresh and monitor fallback",window_source_workflow)
            check("pending Yo/dealer visible in compact canvas and Mini Mesa",compact_pending_hands)
            assert app.executor.mode=="off" and not app.executor.armed
            app.update_idletasks()
            required_controls=("Individual", "Multijugador", "Calibrate Regions",
                              "Start Detection", "New Round", "New Shoe", "Mini Mesa")
            visible_text=" ".join(str(widget.cget("text")) for widget in widgets(app)
                                  if isinstance(widget, (tk.Label, tk.Button)))
            mode_values=tuple(app.mode_combo.cget("values"))
            missing=[label for label in required_controls
                     if label not in mode_values and label not in visible_text]
            assert not missing, f"Required UI controls not constructed: {missing}"
            assert app.winfo_exists() and app.winfo_width()>0 and app.winfo_height()>0
        except Exception:
            callback_errors.append(traceback.format_exc())
        finally:
            close_tops()
            app.controller.stop()
            thread=app.controller._thread
            if thread and thread.is_alive():thread.join(timeout=5)
            payload={"scope":"Functional Tk button test; synthetic capture; isolated profile",
                "results":results,"callback_errors":callback_errors,"messages":messages,
                "recognition_accuracy_tested":False,"executor":"off","isolated_profile":str(isolated),
                "window_constructed":bool(app.winfo_exists()),
                "window_geometry":([app.winfo_width(),app.winfo_height()] if app.winfo_exists() else None)}
            write_json(ROOT/"reports/gui_smoke.json",payload)
            app._on_close()
            ModelProvider.get=original_provider_get
    app.after(1000,run)
    app.mainloop()
    failed=sum(not row["passed"] for row in results)
    print(json.dumps({"features":len(results),"failed":failed,"callback_errors":len(callback_errors),
        "reported_errors":sum(m["type"]=="error" for m in messages)}))
    if failed or callback_errors or any(m["type"]=="error" for m in messages): raise SystemExit(1)


if __name__=="__main__": main()
