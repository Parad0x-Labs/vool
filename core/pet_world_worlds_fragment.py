"""Portable world preview and explicit, archived native Yard replacement."""
from __future__ import annotations

import html
import json

from core.i18n.catalog import catalog_for
from core.pet_cosmetics_fragment import art_data


def render_world_controls(locale):
    catalog = catalog_for(locale)
    def t(key):
        return html.escape(catalog.text('yard.'+key))
    art = json.dumps(art_data(), separators=(',', ':')).replace('<', '\\u003c')
    return f'''<section class="yard-card" id="yardWorlds" style="margin:20px"><h2>{t('worlds')}</h2><p class="yard-help">{t('world_help')}</p><button id="worldExport" disabled>{t('export')}</button><label>{t('world_file')} <input id="worldFile" type="file" accept=".voolworld,application/json"></label><p id="worldStatus" role="status" aria-live="polite"></p><label>{t('room_invite')} <input id="worldInvitation" type="url" autocomplete="off"></label><button id="worldJoin">{t('room_join')}</button></section><dialog id="worldPreview" style="width:min(900px,95vw);max-width:95vw"><h2 id="worldName"></h2><p id="worldDescription"></p><canvas id="worldCanvas" width="832" height="640" style="width:100%;image-rendering:pixelated" aria-label="{t('preview')}"></canvas><p id="worldMissing" role="status"></p><button id="worldUse">{t('world_use')}</button><button id="worldCancel">{t('cancel')}</button></dialog><script type="application/json" id="worldArt">{art}</script>'''


WORLD_JS = r"""
(()=>{'use strict';const $=id=>document.getElementById(id),pc=window.VoolPetCosmetics,t=pc.t,art=JSON.parse($('worldArt').textContent);let raw=null,preview=null,images=new Map(),version=0;
const status=k=>{$('worldStatus').textContent=t(k);};
async function request(body){const r=await fetch('/api/pet-yard',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}),j=await r.json();if(!r.ok||!j.ok)throw Error(j.error||'unavailable');return j;}
function clear(){preview?.destroy();preview=null;for(const img of images.values())img.onload=null;images.clear();raw=null;$('worldPreview').close();}
function draw(g,id){let url,index=0,width=48;const pet=pc.meta(id);if(pet){url='/api/pet-cosmetics/assets/'+pet.atlas.sha256;width=pet.atlas.width;index=pet.frames.idle.front[0];}else if(art[id]){url=art[id].url;index=art[id].poses.idle;}else return false;let img=images.get(url);if(!img){img=new Image();images.set(url,img);img.onload=()=>{version++;preview?.frame(0);};img.src=url;}if(!img.complete||!img.naturalWidth)return false;g.drawImage(img,(index%(width/48))*48,Math.floor(index/(width/48))*48,48,48,0,0,48,48);return true;}
$('worldFile').onchange=async()=>{clear();const file=$('worldFile').files[0];try{if(!file||file.size>270336)throw Error('size');const value=await file.text(),j=await request({action:'preview_world',document:value});raw=value;$('worldName').textContent=j.world.name;$('worldDescription').textContent=j.world.description;$('worldMissing').textContent=j.missing.length?t('world_missing'):t('world_help');$('worldUse').disabled=!!j.missing.length||VoolYardPage.read_only;preview=VoolYard.createRenderer($('worldCanvas'),{yard:j.yard,catalog:VoolYardPage.renderer.catalog,reducedMotion:true,drawPet:draw,mediaVersion:()=>version});preview.setPets(j.yard.pets);preview.frame(0);$('worldPreview').showModal();status('ready');}catch(e){clear();status(e.message);}};
$('worldCancel').onclick=clear;$('worldPreview').addEventListener('cancel',clear);
$('worldUse').onclick=async()=>{if(!raw||!confirm(t('world_confirm')))return;$('worldUse').disabled=true;try{const j=await request({action:'import_world',document:raw,revision:VoolYardPage.yard.revision,stamp:VoolYardPage.stamp,confirmed:true});await VoolYardPage.load(j);clear();status('saved');}catch(e){status(e.message);$('worldUse').disabled=VoolYardPage.read_only;}};
$('worldExport').onclick=async()=>{try{const yard=JSON.parse(JSON.stringify(VoolYardPage.yard));yard.pets=VoolYardPage.controller.serialize();const j=await request({action:'export_world',document:JSON.stringify(yard),description:''}),url=URL.createObjectURL(new Blob([j.document],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download='yard.voolworld';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){status(e.message);}};
$('worldJoin').onclick=()=>{try{const u=new URL($('worldInvitation').value);if(u.username||u.password||!/^\/w\/[a-f0-9]{24}$/.test(u.pathname)||!/^#invite=[A-Za-z0-9_-]{43}$/.test(u.hash)||!(u.protocol==='https:'||u.protocol==='http:'&&['localhost','127.0.0.1','[::1]'].includes(u.hostname)))throw Error('invalid');window.open(u.href,'_blank','noopener,noreferrer');}catch(_){status('invalid');}};
window.addEventListener('vool-yard-ready',()=>{$('worldExport').disabled=false;});if(VoolYardPage.renderer)$('worldExport').disabled=false;
})();
"""
