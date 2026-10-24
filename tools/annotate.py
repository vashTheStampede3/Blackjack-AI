"""Local-only box/rank review. No casino control and no network calls.

Drag a box, choose rank; right click deletes. Save reviewed is explicit.
Card IDs must be stable across frames of the same round for temporal scoring.
"""
import argparse
import json
from pathlib import Path
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageTk

from tools.common import manifest
from lib.logic.vision_labels import RANKS


def main(args):
    path = Path(args.manifest)
    rows = manifest(path)
    if not rows or any(not r.get("redacted") for r in rows):
        raise ValueError("Only redacted ROI recordings may be reviewed")
    labels_path = path.parent/"labels.jsonl"
    labels = {r["image"]:r for r in manifest(labels_path)} if labels_path.exists() else {}
    proposed = {r["image"]:r["predictions"] for r in manifest(args.predictions)} if args.predictions else {}
    root = tk.Tk()
    root.title("Blackjack: revisión local de rangos")
    canvas = tk.Canvas(root,width=1100,height=650,bg="#222")
    canvas.pack()
    bar = ttk.Frame(root); bar.pack(fill="x")
    rank = tk.StringVar(value="unknown"); rid = tk.StringVar(); cid = tk.StringVar()
    cross = tk.BooleanVar(value=False); info = tk.StringVar()
    ttk.Label(bar,text="Rango").pack(side="left")
    ttk.Combobox(bar,textvariable=rank,values=[*RANKS,"unknown"],width=9,state="readonly").pack(side="left")
    ttk.Label(bar,text="Ronda").pack(side="left")
    ttk.Entry(bar,textvariable=rid,width=10).pack(side="left")
    ttk.Label(bar,text="ID carta estable").pack(side="left")
    ttk.Entry(bar,textvariable=cid,width=20).pack(side="left")
    ttk.Checkbutton(bar,text="Revisión cruzada",variable=cross).pack(side="left")
    state = {"index":0,"cards":[],"selected":None,"scale":1,"start":None}

    def draw():
        canvas.delete("all")
        canvas.create_image(0,0,image=state["photo"],anchor="nw")
        s=state["scale"]
        for i,c in enumerate(state["cards"]):
            color="yellow" if i==state["selected"] else "cyan"
            canvas.create_rectangle(*(v*s for v in c["box"]),outline=color,width=2)
            canvas.create_text(c["box"][0]*s,c["box"][1]*s,anchor="sw",text=c["rank"],fill=color)

    def load():
        row=rows[state["index"]]; label=labels.get(row["image"],{})
        image=Image.open(path.parent/row["image"]).convert("RGB")
        state["scale"]=min(1100/image.width,650/image.height,2)
        image=image.resize((int(image.width*state["scale"]),int(image.height*state["scale"])))
        state["photo"]=ImageTk.PhotoImage(image)
        state["cards"]=[dict(c) for c in label.get("cards",proposed.get(row["image"],[]))]
        state["selected"]=None
        rid.set(str(label.get("round_id",""))); cid.set("")
        cross.set(bool(label.get("cross_reviewed",False)))
        info.set(f"{state['index']+1}/{len(rows)} {row['image']} | arrastrar=caja, clic=seleccionar, botón derecho=borrar")
        draw()

    def press(event):
        x,y=event.x/state["scale"],event.y/state["scale"]
        found=[i for i,c in enumerate(state["cards"]) if c["box"][0]<=x<=c["box"][2] and c["box"][1]<=y<=c["box"][3]]
        if found:
            state["selected"]=found[-1]
            card=state["cards"][found[-1]]; rank.set(card["rank"]); cid.set(card.get("card_id",""))
            state["start"]=None; draw()
        else:
            state["start"]=(x,y)

    def release(event):
        if state["start"] is None:
            return
        x,y=event.x/state["scale"],event.y/state["scale"]
        a,b=state["start"]; state["start"]=None
        if abs(x-a)>3 and abs(y-b)>3:
            state["cards"].append({"rank":rank.get(),"box":[min(a,x),min(b,y),max(a,x),max(b,y)],"card_id":cid.get()})
            state["selected"]=len(state["cards"])-1; draw()

    def apply():
        i=state["selected"]
        if i is not None:
            state["cards"][i].update(rank=rank.get(),card_id=cid.get()); draw()

    def remove(event):
        press(event)
        if state["selected"] is not None:
            state["cards"].pop(state["selected"]); state["selected"]=None; draw()

    def save(step=1):
        apply(); row=rows[state["index"]]
        labels[row["image"]]={"image":row["image"],"session":row["session"],"round_id":rid.get() or None,
            "reviewed":True,"cross_reviewed":cross.get(),"cards":state["cards"]}
        tmp=labels_path.with_suffix(".jsonl.tmp")
        tmp.write_text("".join(json.dumps(r)+"\n" for r in labels.values()),encoding="utf-8"); tmp.replace(labels_path)
        state["index"]=min(len(rows)-1,max(0,state["index"]+step)); load()

    canvas.bind("<ButtonPress-1>",press); canvas.bind("<ButtonRelease-1>",release); canvas.bind("<Button-3>",remove)
    ttk.Button(bar,text="Aplicar rango/ID",command=apply).pack(side="left")
    ttk.Button(bar,text="Guardar revisado →",command=save).pack(side="left")
    ttk.Button(bar,text="← Anterior",command=lambda:save(-1)).pack(side="left")
    ttk.Label(root,textvariable=info).pack()
    load(); root.mainloop()


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--manifest",required=True); p.add_argument("--predictions")
    main(p.parse_args())
