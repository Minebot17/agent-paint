"""A reproducible illustration made only with the public engine API. No imported art."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import random
import time

from csp_headless.engine import Engine


def illustration(output,state_root):
    e=Engine(state_root/"demo-state")
    c=e.call
    c("paint_document",action="create",width=900,height=1200,name="Stillwater · headless study",background="#101D2B")
    c("paint_layer",action="update",properties={"name":"Sky / paper"})
    c("paint_fill",kind="linear",color="#091628",color2="#3B676C",start=[0,0],end=[0,1050])
    rng=random.Random(54)
    c("paint_layer",action="create",name="Night dust")
    stars=[]
    for _ in range(110):
        x,y=rng.uniform(50,850),rng.uniform(80,590)
        stars.append({"points":[[x,y]],"color":"#D2E4CC","brush":{"size":rng.uniform(.8,2.8),"opacity":rng.uniform(.15,.65),"pressure_size":False}})
    c("paint_strokes",strokes=stars)
    c("paint_layer",action="create",name="Amber moon",kind="vector")
    c("paint_shape",shape="ellipse",points=[[355,198],[675,518]],fill="#F6B87B")
    c("paint_shape",shape="ellipse",points=[[391,219],[646,474]],fill="#F3C68F")
    c("paint_layer",action="create",name="Far ridges",kind="vector")
    c("paint_shape",shape="polygon",points=[[-30,680],[80,548],[145,590],[280,390],[345,482],[390,436],[535,638],[670,472],[970,688],[970,900],[-30,900]],fill="#447779")
    c("paint_shape",shape="polygon",points=[[146,590],[280,390],[257,491],[310,468],[343,532],[360,536],[416,612]],fill="#81A09A")
    c("paint_shape",shape="polygon",points=[[563,594],[670,472],[650,537],[679,524],[753,604]],fill="#6C9491")
    c("paint_layer",action="create",name="Low mist")
    c("paint_strokes",strokes=[{"points":[[-50,679],[950,679]],"color":"#8EB4A5","brush":{"kind":"airbrush","size":145,"opacity":.32,"pressure_size":False}}])
    c("paint_layer",action="create",name="Near ridges",kind="vector")
    c("paint_shape",shape="polygon",points=[[-30,658],[55,626],[150,686],[220,652],[305,732],[430,760],[510,731],[574,701],[640,722],[748,625],[950,688],[950,870],[-30,870]],fill="#214D57")
    c("paint_layer",action="create",name="Still water")
    c("paint_selection",action="rectangle",rectangle=[0,768,900,1200])
    c("paint_fill",kind="linear",color="#3C7272",color2="#102E3E",start=[0,768],end=[0,1200])
    c("paint_selection",action="none")
    c("paint_layer",action="create",name="Light on water")
    strokes=[]
    for i in range(44):
        y=779+i*7.8
        center=493+rng.uniform(-25,25)
        half=20+i*1.7+rng.uniform(0,28)
        strokes.append({"points":[[center-half,y,.05],[center-half*.6,y,1],[center+half*.6,y+rng.uniform(-1,1),.8],[center+half,y,.02]],
            "color":"#E3B585" if i<24 else "#80A6A0", "brush":{"kind":"pencil","size":rng.uniform(2,5),"opacity":.36,"texture":.45,"seed":i}})
    c("paint_strokes",strokes=strokes)
    c("paint_layer",action="create",name="Pine silhouettes")
    strokes=[]
    for x,y,height in [(75,829,240),(151,843,170),(813,821,280),(758,838,160)]:
        strokes.append({"points":[[x,y,.8],[x-7,y-height,.02]],"color":"#102D39","brush":{"size":8,"taper_end":.5}})
        for j in range(11):
            yy=y-height+25+j*height/12
            spread=12+j*3.8
            for direction in (-1,1):
                strokes.append({"points":[[x-5,yy,.8],[x+direction*spread,yy+19,.01]],"color":"#102D39",
                    "brush":{"size":7+j*.9,"taper_end":.65}})
    c("paint_strokes",strokes=strokes)
    c("paint_layer",action="create",name="Foreground reeds")
    strokes=[]
    for i in range(40):
        x=rng.uniform(-20,240) if i<20 else rng.uniform(700,940)
        y=rng.uniform(1000,1200); height=rng.uniform(100,260); dx=rng.uniform(-45,45)
        strokes.append({"points":[[x,y,1],[x+dx*.5,y-height*.6,.65],[x+dx,y-height,.01]],"color":"#0A2635","brush":{"size":rng.uniform(4,11),"taper_end":.8}})
    c("paint_strokes",strokes=strokes)
    c("paint_text",text="S T I L L W A T E R",x=75,y=70,style={"font_size":26,"color":"#D1D9C6"})
    c("paint_text",text="A study in quiet layers",x=77,y=116,style={"font_size":14,"color":"#89A5A3"})
    c("paint_text",text="AGENT PAINT",x=76,y=1110,style={"font_size":16,"color":"#BCD0BB"})
    c("paint_text",text="NO WINDOW · NO MOUSE · DOCUMENT PIXELS",x=76,y=1138,style={"font_size":10,"color":"#769A9B"})
    c("paint_region",name="moon-and-ridge",rectangle=[260,170,720,690])
    c("paint_document",action="save",path=str(output/"headless-stillwater.apaint"),overwrite=True)
    c("paint_document",action="export",path=str(output/"headless-stillwater.png"),overwrite=True)
    c("paint_document",action="export",path=str(output/"headless-stillwater.ora"),overwrite=True)
    c("paint_document",action="export",path=str(output/"headless-stillwater.psd"),overwrite=True)
    snap=c("paint_snapshot",region="moon-and-ridge")
    return {"layers":len(e.doc.layers),"size":e.doc.size,"detail_snapshot":snap.info["image_path"]}


def brush_sheet(output,state_root):
    e=Engine(state_root/"brush-demo-state"); c=e.call
    c("paint_document",action="create",width=1100,height=820,name="Brush behavior",background="#F1E7D1")
    c("paint_text",text="MARKS / PRESSURE / MATERIAL",x=48,y=30,style={"font_size":24,"color":"#1B3943"})
    kinds=["round","pencil","airbrush","watercolor","marker","eraser","smudge"]
    for i,kind in enumerate(kinds):
        y=126+i*91
        c("paint_layer",action="create",name=kind)
        if kind in {"eraser","smudge"}:
            c("paint_shape",shape="rectangle",points=[[250,y-28],[1035,y+29]],fill="#C18B60")
            if kind=="smudge":
                c("paint_shape",shape="ellipse",points=[[480,y-24],[535,y+25]],fill="#236D70")
        c("paint_strokes",strokes=[{"points":[[270,y,.08],[400,y-15,.6],[590,y+5,1],[800,y-12,.5],[1010,y,.02]],
            "color":"#236D70","brush":{"kind":kind,"size":39,"hardness":.6 if kind in {"airbrush","watercolor","smudge"} else 1,
                "opacity":.55 if kind in {"marker","watercolor"} else 1,"taper_end":.2,"seed":4}}])
        c("paint_text",text=kind.upper(),x=48,y=y-10,style={"font_size":17,"color":"#1B3943"})
    c("paint_document",action="save",path=str(output/"headless-brushes.apaint"),overwrite=True)
    c("paint_document",action="export",path=str(output/"headless-brushes.png"),overwrite=True)


def benchmark(output,state_root):
    e=Engine(state_root/"benchmark-state"); c=e.call
    c("paint_document",action="create",width=1024,height=1024)
    rng=random.Random(42)
    strokes=[]
    for _ in range(500):
        x,y=rng.uniform(20,950),rng.uniform(20,950)
        strokes.append({"points":[[x,y,.1],[x+15,y+10,.6],[x+30,y,1],[x+40,y+20,.1]],
                        "color":"#2E6870","brush":{"size":12,"opacity":.5}})
    start=time.perf_counter(); c("paint_strokes",strokes=strokes); elapsed=time.perf_counter()-start
    return {"canvas":[1024,1024],"strokes":500,"elapsed_seconds":round(elapsed,4),"strokes_per_second":round(500/elapsed),
            "timing_note":"Includes transaction snapshot and render, excludes project export; no deliberate waits"}


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--output",type=Path,default=Path("artifacts/demo"))
    parser.add_argument("--state-dir",type=Path)
    args=parser.parse_args(); args.output.mkdir(parents=True,exist_ok=True)
    state_root=args.state_dir or args.output/"state"
    start=time.perf_counter()
    result={"illustration":illustration(args.output,state_root),"benchmark":benchmark(args.output,state_root)}
    brush_sheet(args.output,state_root)
    result["total_demo_seconds"]=round(time.perf_counter()-start,3)
    (args.output/"demo-report.json").write_text(json.dumps(result,indent=2),"utf-8")
    print(json.dumps(result,indent=2))


if __name__=="__main__": main()
