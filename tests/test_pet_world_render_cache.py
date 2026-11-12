"""Cached edits must retain the pixels, depth picking and ground of a fresh world."""
from __future__ import annotations

import json
import os
from pathlib import Path

from tests._toybox_v2_browser import finish_browser, network_guard
from tests._toybox_v2_browser import served as served
from tests.served_browser import launch_chromium


def test_edited_columns_match_fresh_render_including_in_place_mutation(served):
    manager, browser = launch_chromium()
    try:
        page = browser.new_page(viewport={'width': 1400, 'height': 1000})
        outside = network_guard(page, served)
        page.goto(served+'/pet-yard')
        page.wait_for_function('window.VoolYardPage?.renderer && VoolYardPage.controller')
        results = page.evaluate(r"""()=>{
          const r=VoolYardPage.renderer;
          VoolYardPage.controller.stop();r.setPets([]);r.setToy(null);r.setGhost(null);r.setCamera(1,0,0);
          const yard=structuredClone(r.yard),index=8*24+10,results=[];
          const changes=[
            ()=>{yard.columns[index]=[['stone',2],['wood',1]];},
            ()=>{yard.columns[index][1][1]=5;},
            ()=>{yard.columns[index][1][0]='clay';},
            ()=>{yard.columns[index]=[];},
            ()=>{yard.columns[index]=[['water',2]];},
            ()=>{yard.columns[3*24+3]=[['water',1]];yard.columns[3*24+4]=[['water',1]];yard.decorations.push({id:'bridge',x:3,y:3,rotation:1});},
            ()=>{yard.decorations.at(-1).rotation=2;yard.columns[index]=[['grass',1]];}
          ];
          for(const change of changes){
            change();r.setYard(yard);r.frame(0);
            const canvas=document.createElement('canvas');canvas.width=r.canvas.width;canvas.height=r.canvas.height;
            const fresh=VoolYard.createRenderer(canvas,{yard:structuredClone(yard),catalog:r.catalog,reducedMotion:true});
            fresh.setPets([]);fresh.frame(0);
            const a=r.canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;
            const b=canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;
            let different=0;for(let i=0;i<a.length;i++)if(a[i]!==b[i])different++;
            const picks=renderer=>[[10,8],[3,3],[2,2],[22,22]].map(([x,y])=>{const p=renderer.project(x,y);return renderer.pick(p.x,p.y);});
            results.push({different,ground:JSON.stringify(r.ground)===JSON.stringify(fresh.ground),
              picks:JSON.stringify(picks(r))===JSON.stringify(picks(fresh)),tile_rebuilds:r.metrics.tile_rebuilds});
            fresh.destroy();
          }
          return results;
        }""")
        (Path(os.environ['TMPDIR'])/'yard-cache-equivalence.json').write_text(json.dumps(results, indent=2))
        assert len(results) == 7
        assert all(r['different'] == 0 and r['ground'] and r['picks'] for r in results), results
        assert not outside()
    finally:
        finish_browser(manager, browser)
