"""Consumer add-on store, backed by the existing review and plugin authorities."""


def render_addon_store_fragment() -> str:
    return r'''
<style>
#pluginsOverlay .modal{width:min(1080px,94vw);max-width:1080px;height:min(820px,88vh);display:flex;flex-direction:column;border-radius:8px}
#pluginsOverlay .modal-head{padding:18px 24px;border-bottom:1px solid var(--border);flex:none;gap:14px;flex-wrap:wrap}
#pluginsTitle{font-size:18px;letter-spacing:-.3px;flex:1}
#addonBack{background:none;border:1px solid var(--border);border-radius:4px;padding:7px 10px;color:inherit;font:inherit;font-size:12px;cursor:pointer}
#addonBack[hidden]{display:none}#addonBack:disabled{opacity:.45;cursor:default}#addonBack:focus-visible{outline:2px solid var(--accent);outline-offset:3px}
#pluginsOverlay .modal-body{padding:0;display:grid;grid-template-columns:172px minmax(0,1fr);grid-template-rows:auto minmax(0,1fr);flex:1;min-height:0;overflow:hidden}
#pluginsOverlay .modal-body>.set-help{display:none}
#addonSearchBar{grid-column:2;grid-row:1;display:flex;gap:8px;margin:20px 24px 0;min-width:0}#addonSearchBar[hidden]{display:none}#addonSearchBar>button{flex:none;background:var(--panel);color:inherit;border:1px solid var(--border);border-radius:4px;padding:0 14px;font:inherit;font-size:12px}#pluginsSearch{width:100%;min-width:0;margin:0;padding:12px 14px;border-radius:5px;background:var(--bg);box-sizing:border-box}
#pluginsBody{grid-column:2;grid-row:2;min-width:0;overflow:auto;padding:24px;overscroll-behavior:contain}
#addonNav{grid-column:1;grid-row:1 / 3;margin:0;padding:24px 12px;border-right:1px solid var(--border);display:flex;flex-direction:column;gap:5px;background:rgba(127,127,127,.025)}
#addonNav button{background:none;border:0;border-radius:4px;color:var(--muted);text-align:left;padding:11px 12px;cursor:pointer;font-weight:600;font-size:13px}
#addonNav button[aria-pressed=true]{background:rgba(127,127,127,.14);color:var(--fg,#eee)}
.addon-nav-note{margin:auto 10px 0;font-size:11px;line-height:1.65;color:var(--muted)}
.addon-hero{display:block;position:static;background:none;border:0;padding:6px 0 22px}.addon-eyebrow{text-transform:uppercase;letter-spacing:1.5px;font-size:10px;font-weight:700;color:var(--muted)}
.addon-hero h2{font-size:29px;line-height:1.2;font-weight:650;letter-spacing:-.8px;margin:10px 0}.addon-hero p{font-size:14px;line-height:1.6;color:var(--muted);max-width:530px;margin:0}
.addon-controls{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:0 0 20px}
.addon-controls button,.addon-controls select,.addon-actions button,.addon-linkbtn,.addon-card button,.addon-review button{background:var(--panel,#171b22);color:inherit;border:1px solid var(--border,#38414b);padding:9px 13px;border-radius:4px;cursor:pointer;font:inherit;font-size:12px}
.addon-controls button[aria-pressed=true]{background:var(--fg,#eee);color:var(--bg,#111);border-color:transparent}
.addon-controls select{margin-left:auto}.media-studio-card{display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between;border:1px solid var(--border);border-radius:12px;padding:14px 16px;background:var(--panel);color:var(--ink)}.media-studio-card a{font-weight:600;color:var(--accent)}.media-studio-card .p-desc{margin:0;flex:1 1 240px}
.addon-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
.addon-card{border:1px solid var(--border,#38414b);border-radius:6px;padding:20px;background:var(--panel,#171b22);min-width:0;display:flex;flex-direction:column;gap:12px}
.addon-card:hover{border-color:var(--muted)}.addon-card h3{margin:0;font-size:16px;letter-spacing:-.2px}.addon-card p{margin:0;line-height:1.55;font-size:13px;color:var(--muted)}
.addon-card-head{display:flex;align-items:center;gap:13px}.addon-icon{width:52px;height:52px;flex:none;display:grid;place-items:center;border-radius:6px;background:#303d4b;color:#dce6f2;font-size:22px;font-weight:650;letter-spacing:-1px}
.skill-report{margin-top:16px}.skill-report p{margin:0}.skill-report a,.skill-report-row a{color:var(--muted);text-underline-offset:2px}.skill-report-row{display:flex;justify-content:flex-end;margin-top:-4px;font-size:11px}.skill-report-row a{text-decoration:none}.skill-report-row a:hover,.skill-report-row a:focus-visible{text-decoration:underline}.skill-paused{margin:0;padding:9px 12px;border:1px solid var(--border,#38414b);border-radius:6px}
.skill-publish-row{display:flex;justify-content:flex-end;margin-top:4px}.skill-publish-btn,.skill-publish-quiet{background:none;border:0;padding:0;color:var(--muted);font:inherit;font-size:11px;cursor:pointer;text-underline-offset:2px}.skill-publish-btn{padding:6px 0;min-height:28px}.skill-publish-btn:hover,.skill-publish-btn:focus-visible,.skill-publish-quiet{text-decoration:underline}.skill-publish-quiet{font-size:12px}.skill-publish .addon-actions .skill-publish-quiet{text-decoration:none}.skill-publish h4:focus{outline:none}.skill-publish h4:focus-visible{outline:2px solid var(--accent);outline-offset:3px}
.skill-publish{margin-top:12px;padding:14px 16px;border:1px solid var(--border,#38414b);border-radius:6px;font-size:13px;line-height:1.55}.skill-publish h4{margin:0 0 8px;font-size:13px}.skill-publish p{margin:8px 0}.skill-publish ul{margin:6px 0 10px;padding-inline-start:18px}.skill-publish li{margin:4px 0}.skill-publish li small{display:block;color:var(--muted);font-size:11px;overflow-wrap:anywhere}.skill-publish-quote{display:block;margin-top:4px;padding:4px 6px;border:1px solid var(--border,#38414b);border-radius:4px;font-size:11px;white-space:pre-wrap;overflow-wrap:anywhere}.skill-publish li,.skill-publish-quote,.skill-publish-text{unicode-bidi:plaintext;text-align:start}.skill-publish .addon-facts{margin:6px 0 10px}.skill-publish .addon-actions{margin:12px 0 0}
.skill-publish-text{white-space:pre-wrap;overflow-wrap:anywhere;max-height:220px;overflow:auto;font-size:11px;margin:10px 0;padding:10px;border:1px solid var(--border,#38414b);border-radius:4px;background:var(--bg,#101319)}#pluginsBody a.skill-publish-open{display:inline-block;padding:9px 13px;border-radius:4px;text-decoration:none;font-size:12px}#pluginsBody a.skill-publish-open:focus-visible{outline:2px solid var(--accent);outline-offset:3px}
.addon-icon.skill-pic{position:relative;overflow:hidden;background:var(--field,#1d2129);color:var(--muted)}.addon-icon.skill-pic::after{content:"";position:absolute;inset:0;border-radius:inherit;box-shadow:inset 0 0 0 1px var(--border,#38414b);pointer-events:none}.addon-icon.skill-pic img{width:100%;height:100%;object-fit:cover;display:block;animation:skill-pic-in .2s ease-out}@keyframes skill-pic-in{from{opacity:0}}@media (prefers-reduced-motion:reduce){.addon-icon.skill-pic img{animation:none}}.addon-icon.skill-missing svg{width:50%;height:50%;opacity:.7}.addon-card-head h3{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}.addon-icon.design{background:#483a33;color:#f4d5bf}.addon-icon.security{background:#353d35;color:#d0dfbe}.addon-card-head small{display:block;color:var(--muted);font-size:11px;margin-top:5px}
.addon-card-footer{display:flex;gap:8px;align-items:center;justify-content:space-between;margin-top:auto;padding-top:8px;font-size:11px;color:var(--muted)}
.addon-state{font-size:11px;color:var(--muted)}.addon-state.good{color:var(--accent,#48d6be)}
.addon-risk-note{font-size:12px;line-height:1.6;padding:12px;border:1px solid #76563d}.addon-risk-note button{margin:0 0 0 10px}.addon-override{padding-top:16px;border-top:1px solid var(--border)}.addon-override summary{font-weight:650;color:var(--ink)}.addon-override label{display:flex;gap:10px;align-items:flex-start;font-size:13px;color:var(--ink);margin:18px 0}.addon-override input{margin-top:4px;flex:none}.addon-override .addon-risk-button{border-color:#9d7043;color:var(--warning,#ffbd67)}
.addon-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:18px 0}
#pluginsBody .addon-primary{background:var(--fg,#e8ecef);color:var(--bg,#101319);border-color:transparent;font-weight:650}
.addon-linkbtn{margin-bottom:20px;background:none;border:0;padding:0;color:var(--muted)}
.addon-detail-head{display:flex;gap:16px;align-items:center}.addon-detail-head .addon-icon{width:68px;height:68px;font-size:28px}.addon-detail-head h2{font-size:27px;letter-spacing:-.6px;margin:0 0 5px}.addon-detail-head p{margin:0;color:var(--muted);font-size:12px}
.addon-detail{max-width:680px}.addon-detail>p{line-height:1.7;font-size:14px}.addon-facts{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;padding:18px 0;border-top:1px solid var(--border);border-bottom:1px solid var(--border);margin:24px 0;font-size:13px}.addon-facts dt{color:var(--muted);font-size:11px;margin-bottom:8px}.addon-facts dd{margin:0}
.addon-detail h3{font-size:14px;margin:22px 0 10px}.addon-detail details{margin:20px 0;color:var(--muted);font-size:12px;line-height:1.7}.addon-detail summary{cursor:pointer}.addon-detail a{color:inherit}.addon-detail pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:220px;overflow:auto;font-size:11px}
.addon-meta{font-size:12px;color:var(--muted);line-height:1.6}.addon-facts{display:grid;grid-template-columns:auto 1fr;gap:4px 10px;margin:0;font-size:12px;line-height:1.5}.addon-facts dt{color:var(--muted)}.addon-facts dd{margin:0;overflow-wrap:anywhere}.addon-error{padding:14px;border:1px solid #76563d;background:rgba(173,110,50,.08);color:var(--warning,#ffbd67);font-size:13px;line-height:1.5;margin-bottom:16px}
.addon-review{padding:20px;border:1px solid var(--border);border-radius:6px;margin:20px 0}.addon-review h3{margin:0 0 10px}.addon-review p{line-height:1.6;font-size:13px}.addon-review.blocked{border-color:#88544b;background:rgba(172,65,45,.055)}
.addon-localcheck{padding:18px 20px;border:1px dashed var(--border,#38414b);border-radius:6px;margin:20px 0}.addon-localcheck>h3{margin:0 0 10px;font-size:14px}.addon-localcheck p{line-height:1.6;font-size:13px}.addon-localcheck .addon-findings strong{display:block;margin-bottom:5px}.addon-localcheck details{margin:10px 0 0;color:var(--muted);font-size:12px;line-height:1.7}.addon-localcheck summary{cursor:pointer}
.addon-detail .addon-risk-explanation{margin:14px 0 0;padding:12px 14px;border:1px solid var(--border);border-radius:4px}.addon-risk-explanation summary{color:var(--ink);font-weight:600}.addon-risk-explanation .addon-meta{font-size:11px}.addon-risk-explanation p:last-child{margin-bottom:0}
.addon-brand{color:var(--ink)}
.addon-findings{list-style:none;padding:0;margin:18px 0}.addon-findings li{padding:14px 0;border-top:1px solid var(--border);font-size:13px;overflow-wrap:anywhere}.addon-findings strong{display:block;margin-bottom:5px}.addon-findings small{display:block;margin-top:8px;color:var(--muted)}
.addon-pending{border:1px solid var(--border);padding:14px 16px;display:flex;align-items:center;justify-content:space-between;gap:12px;margin:0 0 12px;border-radius:4px;font-size:13px}.addon-pending small{display:block;color:var(--muted);margin-top:5px}
#pluginsBody button:disabled{opacity:.45;cursor:default}#pluginsBody button:focus-visible,#addonNav button:focus-visible{outline:2px solid var(--accent);outline-offset:3px}
.addon-collections{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-bottom:22px}.addon-collections button{border:1px solid var(--border);background:var(--panel);color:inherit;padding:15px 10px;text-align:left;border-radius:5px;cursor:pointer}.addon-collections strong{display:block;font-size:13px}.addon-collections small{display:block;color:var(--muted);font-size:11px;margin-top:7px}.addon-collections button[aria-pressed=true]{border-color:var(--muted);background:rgba(127,127,127,.12)}.addon-library-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.addon-library-grid>.plugins-empty{grid-column:1 / -1}.addon-library-grid .plugin-card{margin:0;border-radius:6px;padding:18px}.addon-library-grid .p-head{flex-wrap:wrap}.addon-library-grid .p-desc{line-height:1.6}.addon-library-grid .p-ver{display:none}
@media(max-width:740px){#pluginsOverlay .modal-body{grid-template-columns:1fr;grid-template-rows:auto auto minmax(0,1fr)}#addonNav{grid-column:1;grid-row:1;flex-direction:row;border-right:0;border-bottom:1px solid var(--border);padding:10px;flex-wrap:wrap}.addon-nav-note{display:none}#addonSearchBar{grid-column:1;grid-row:2;margin:12px 16px 0;flex-wrap:wrap}#pluginsSearch{flex:1;min-width:120px}#addonSearchBar>button{min-height:40px}#pluginsBody{grid-column:1;grid-row:3;padding:20px 16px}.addon-grid,.addon-library-grid{grid-template-columns:1fr}.addon-collections{grid-template-columns:repeat(2,minmax(0,1fr))}.addon-hero h2{font-size:24px}.addon-facts{gap:8px}.addon-pending{align-items:flex-start}}
</style>
<script>(function(){
'use strict';
const tr=(key,fallback)=>{const k='addons.'+key;const v=window.VOOLT?window.VOOLT(k):k;return v===k?fallback:v;};
const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
let tab='discover',data=null,category='',ecosystem='',limit=24,remote=null,sort='name',availability='ready',selected=null,review=null,busy=false,error='',generation=0,localCheck=null;
let registryData=null,registryReview=null;
// The one skill whose Skill Codex check is open: {slug, data, showText}. Kept here so a re-render keeps the panel.
let publishState=null;
const key=v=>String(v||'').normalize('NFKC').toLowerCase().replace(/[\s_-]+/g,'');
const slugKey=s=>String(s||'').replace(/-/g,'_');
const catLabel=e=>tr('registry_category_'+slugKey(e.category),e.category)+' · '+tr('registry_subcategory_'+slugKey(e.subcategory),e.subcategory);
function button(text,fn,cls){const b=node('button',text,cls||'');b.type='button';b.disabled=busy;b.addEventListener('click',fn);return b;}
async function post(payload){const r=await fetch('/api/addons',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const j=await r.json();if(!r.ok||j.error){const err=new Error((j.message||tr('request_failed','The request could not finish.'))+' ['+(j.error||r.status)+']');err.code=j.error;throw err;}return j;}
async function read(){const r=await fetch('/api/addons');if(!r.ok)throw new Error(tr('catalog_failed','Could not read the add-on catalogue.'));return r.json();}
function refresh(){if(typeof renderPluginPanel==='function')renderPluginPanel(_pluginCatalog||{},document.getElementById('pluginsSearch')?.value||'');}
async function load(){
 const epoch=++generation;data=null;refresh();
 try{
  // One hydration owns the store and installed-library snapshots. A late legacy
  // catalogue response must never rebuild a review the owner already opened.
  const [j,plugins]=await Promise.all([read(),fetch('/api/plugins').then(r=>{if(!r.ok)throw new Error(tr('catalog_failed','Could not read the add-on catalogue.'));return r.json();})]);
  if(epoch!==generation)return;data=j;_pluginCatalog=plugins;
 }catch(e){if(epoch!==generation)return;error=e.message;}
 refresh();
}
function link(label,url){const a=node('a',label);a.href=url;a.target='_blank';a.rel='noopener noreferrer';return a;}
function icon(e){return node('span',e.category==='Design'?'Aa':e.category==='Security'?'◇':'</>', 'addon-icon '+(e.category==='Design'?'design':e.category==='Security'?'security':''));}
function brand(label,tag,source){const n=node(tag||'span',label,'addon-brand');const id=String(source||label||'').toLowerCase();if(['anthropic','superpowers','vercel'].includes(id))n.classList.add(id);return n;}
// A listing id such as "finishing-a-development-branch" reads as "Finishing a development branch".
function listingTitle(e){const n=String((e&&e.name)||'');return /^[a-z0-9]+(?:[-_][a-z0-9]+)+$/.test(n)?n.charAt(0).toUpperCase()+n.slice(1).replace(/[-_]+/g,' '):n;}
function hero(host,title,subtitle,kicker){const h=node('header',undefined,'addon-hero');if(kicker)h.append(node('span',kicker,'addon-eyebrow'));h.append(node('h2',title),node('p',subtitle));host.append(h);}
function nav(){
 const search=document.getElementById('pluginsSearch');if(!search)return;
 let n=document.getElementById('addonNav');if(!n){n=node('nav',undefined,'addon-nav');n.id='addonNav';n.setAttribute('aria-label',tr('navigation','Add-on navigation'));search.before(n);}
 let bar=document.getElementById('addonSearchBar');if(!bar){bar=node('div');bar.id='addonSearchBar';search.before(bar);bar.append(search);search.addEventListener('keydown',ev=>{if(ev.key==='Enter'&&tab==='discover'){ev.preventDefault();ev.stopPropagation();remoteFind();}});}
 let remoteButton=document.getElementById('addonGitHubSearch');if(!remoteButton){remoteButton=button(tr('github_search','Search GitHub'),remoteFind);remoteButton.id='addonGitHubSearch';bar.append(remoteButton);}remoteButton.disabled=busy;remoteButton.hidden=tab!=='discover';
 n.replaceChildren();[['discover',tr('browse','Browse')],['registry',tr('registry_tab','Skill Codex')],['installed',tr('my_addons','My add-ons')],['security',tr('security_tab','Security')]].forEach(([id,label])=>{const b=button(label,()=>{if(busy)return;tab=id;selected=null;review=null;registryReview=null;remote=null;publishState=null;error='';refresh();});b.setAttribute('aria-pressed',String(tab===id));n.append(b);});
 n.append(node('p',tr('nav_note','Skills teach VOOL new approaches. Plugins add tools and connections.'),'addon-nav-note'));
 bar.hidden=tab==='security'||!!selected||!!review||!!registryReview;search.hidden=false;search.placeholder=tr('search_store','Search skills, plugins or paste a GitHub repository…');
 const title=document.getElementById('pluginsTitle');if(title){
  title.textContent=tr('store_title','Skills & Plugins');
  let back=document.getElementById('addonBack');if(!back){back=button('',()=>{if(busy)return;if(registryReview){registryReview=null;}else if(review){review=null;selected=null;}else if(selected){selected=null;}else{remote=null;}error='';refresh();});back.id='addonBack';title.before(back);}
  back.hidden=!(tab==='registry'&&registryReview)&&(tab!=='discover'||!(review||selected||remote));back.disabled=busy;
  back.textContent=review||selected?tr('back_list','← Back to list'):tr('back_directory','← Back to directory');
 }
}
function security(host){
 host.replaceChildren();hero(host,tr('security_title','An extra check before you install'),tr('security_intro','Review third-party instructions before they reach your agent. You decide what gets added.'));
 const card=node('section',undefined,'addon-card');host.append(card);const head=node('div',undefined,'addon-card-head');head.append(icon({category:'Security'}),node('h3','Eyebrow'));card.append(head);
 card.append(node('p',tr('security_help','Scans add-on contents for risks. A passing scan is not a safety guarantee; VOOL permissions still control every tool action.')));
 const state=node('p',tr('loading','Loading…'),'addon-state');card.append(state);
 card.append(button(tr('manage_api_keys','Manage in API Keys'),()=>{if(window.VoolPageActions){window.VoolPageActions.openSettings('keys');return;}location.hash='keys';}));
 card.append(node('p',tr('quota_note','Scans use your Eyebrow allowance. Nothing is scanned in the background.'),'addon-meta'));
 read().then(j=>{if(state.isConnected)state.textContent=j.eyebrow.configured?tr('key_saved','Key saved. Connection is checked when you run a scan.'):tr('no_key','Connect Eyebrow in API Keys to scan external add-ons.');}).catch(e=>{if(state.isConnected)state.textContent=e.message;});
}
async function action(work){if(busy)return;busy=true;error='';refresh();try{await work();}catch(e){error=e.message;}finally{busy=false;refresh();}}
function pending(host){
 if(!(data.reviews||[]).length)return;
 const group=node('details',undefined,'addon-saved-scans');group.append(node('summary',tr('saved_scans','Saved scans to review')+' ('+data.reviews.length+')'));group.style.marginBottom='18px';host.append(group);host=group;
 (data.reviews||[]).forEach(r=>{const row=node('div',undefined,'addon-pending');const label=node('div',r.entry.name);label.append(node('small',r.can_install?tr('ready_review','Scan ready · awaiting your decision'):tr('needs_review','Scan saved · needs review')));row.append(label,button(tr('resume_review','Review scan'),()=>action(async()=>{review=await post({action:'review',review_id:r.review_id});selected=null;}),'addon-linkbtn'));host.append(row);});
}
function render(query){
 const host=document.getElementById('pluginsBody');if(!host)return false;host.replaceChildren();nav();host.classList.remove('addon-library-grid');
 if(tab==='security'){security(host);return true;}
 if(error){const p=node('p',error,'addon-error');p.setAttribute('role','alert');host.append(p);}
 if(tab==='registry'){renderRegistry(host,query);return true;}
 if(!data){host.append(error?button(tr('retry','Try again'),()=>{error='';load();}):node('p',tr('loading','Loading…')));return true;}
 if(tab==='installed'){renderLibrary(host,query);return true;}
 if(review){renderReview(host);return true;}if(selected){renderDetail(host,selected);return true;}if(remote){renderRemote(host);return true;}
 hero(host,tr('discover_title','Discover add-ons'),tr('browse_ready_intro','These skills work as instructions in VOOL. Review the Eyebrow scan before enabling one. Included skills need no GitHub account; some need other apps or accounts.'),tr('curated','THE ADD-ON STORE'));
 const collections=node('div',undefined,'addon-collections');[...new Set(data.entries.map(e=>e.ecosystem||e.publisher))].forEach(source=>{const ready=data.entries.filter(e=>(e.ecosystem||e.publisher)===source&&!e.discovery_only&&e.import_ready!==false).length;if(!ready&&availability==='ready'&&ecosystem!==source)return;const b=button('',()=>{ecosystem=ecosystem===source?'':source;category='';limit=24;refresh();});b.append(brand(source,'strong'),node('small',ready+' '+tr('ready_count','compatible with VOOL')));b.setAttribute('aria-pressed',String(ecosystem===source));collections.append(b);});host.append(collections);
 pending(host);
 const statusControls=node('div',undefined,'addon-controls');[['ready',tr('ready_filter','Compatible with VOOL')],['all',tr('all_filter','All listed skills')]].forEach(([v,label])=>{const b=button(label,()=>{availability=v;limit=24;refresh();});b.setAttribute('aria-pressed',String(availability===v));statusControls.append(b);});host.append(statusControls);
 const controls=node('div',undefined,'addon-controls');const cats=['',...new Set(data.entries.filter(e=>!ecosystem||(e.ecosystem||e.publisher)===ecosystem).map(e=>e.category))];
 cats.forEach(c=>{const b=button(c||tr('all_categories','All categories'),()=>{category=c;limit=24;refresh();});b.setAttribute('aria-pressed',String(c===category));controls.append(b);});
 const ordering=node('select');ordering.setAttribute('aria-label',tr('sort','Sort'));[['name',tr('sort_name','Name')],['category',tr('category','Category')]].forEach(([v,l])=>{const o=node('option',l);o.value=v;ordering.append(o);});ordering.value=sort;ordering.onchange=()=>{sort=ordering.value;refresh();};controls.append(ordering);host.append(controls);
 const rows=data.entries.filter(e=>(availability==='all'||(!e.discovery_only&&e.import_ready!==false))&&(!category||category===e.category)&&(!ecosystem||(e.ecosystem||e.publisher)===ecosystem)&&key([e.name,e.description,e.category,e.publisher,e.ecosystem].join(' ')).includes(key(query))).sort((a,b)=>a[sort].localeCompare(b[sort])||a.name.localeCompare(b.name));
 const grid=node('div',undefined,'addon-grid');host.append(grid);
 // The built-in Media Studio card stays offered in the unified store exactly as it was in the
 // skills panel: first-party, present whether or not anything is installed, hidden only when a
 // search does not name it (same words the old card matched on).
 if(!query||key('media studio editor video audio image tool').includes(key(query))){
  const card=node('article',undefined,'media-studio-card');
  const head=node('div',undefined,'addon-card-head');const name=node('div');name.append(node('h3','Media Studio'),brand('VOOL','small','built-in'));head.append(name);card.append(head,node('p','Cut, trim and export media in a dedicated editor window.'));
  const a=document.createElement('a');a.className='media-studio-open';a.href='/media-editor';a.target='_blank';a.rel='noopener';a.title='Open the Media Studio editor in a dedicated window';a.textContent='Open Media Studio';card.append(a);grid.append(card);
 }if(!rows.length)grid.append(node('p',availability==='ready'?tr('no_ready','No compatible skills match. Choose All listed skills to see what needs support.'):tr('no_results','No matching add-ons.')));
 rows.slice(0,limit).forEach(e=>{const card=node('article',undefined,'addon-card');const head=node('div',undefined,'addon-card-head');const name=node('div');name.append(node('h3',listingTitle(e)),brand(e.publisher,'small',e.ecosystem));head.append(icon(e),name);card.append(head,node('p',e.description));const foot=node('div',undefined,'addon-card-footer');foot.append(node('span',e.license+' · '+(e.discovery_only?tr('listed','Not inspected'):e.import_ready===false?tr('not_supported','Not supported yet'):tr('ready_filter','Compatible with VOOL'))),button(tr('view','View add-on'),()=>{selected=e;refresh();},'addon-primary'));card.append(foot);if(e.installed)card.append(node('span',e.scan==='checked'?tr('checked','Checked by Eyebrow'):e.scan==='risks_accepted'?tr('risk_accepted','Risks accepted by you'):tr('changed','Changed since review'),'addon-state '+(e.scan==='checked'?'good':'')),node('span',e.enabled?tr('enabled','Enabled'):tr('disabled','Disabled'),'addon-state'));grid.append(card);});
 if(rows.length>limit)host.append(button(tr('show_more','Show more')+' ('+(rows.length-limit)+')',()=>{limit+=24;refresh();},'addon-linkbtn'));
 host.append(node('p',tr('scope','Directory entries are not endorsements. Compatibility and licence checks come before scanning. Executable plugins and MCP servers require an adapter.'),'addon-meta'));
 host.append(node('p',tr('github_privacy','Search GitHub sends only the text you enter to GitHub. Browsing this directory stays local.'),'addon-meta'));return true;
}
function nativeControlNotice(host,value){
 host.append(node('p',value.authority_notice||tr('native_authority','Skills contribute instructions. VOOL native rules, owner settings, spending limits, currency handling, permissions and approvals always take precedence.'),'addon-risk-note'));
 (value.native_control_overlaps||[]).forEach(item=>host.append(node('p',item.message,'addon-meta')));
}
// A creator's icon appears only after VOOL checks it against the signed listing and a person approved it for that skill; otherwise our own tile.
const MISSING_PICTURE='<svg viewBox="0 0 48 48" aria-hidden="true" focusable="false"><rect x="8" y="10" width="32" height="28" rx="5" fill="none" stroke="currentColor" stroke-width="2"/><circle cx="18" cy="20" r="3" fill="currentColor"/><path d="M12 33l8-8 5 5 4-4 7 7" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const pictures=new Map(),pictureQueue=[];let picturesRunning=0;
// Skills the registry said are paused since the last Browse; their cards say so instead of offering a download.
const pausedSkills=new Set();
const PAUSED_NOTE=()=>tr('registry_paused','Downloads of this skill are paused while a person looks into it. Please check back later.');
async function unlessPaused(id,work){try{await work();}catch(err){if(err.code!=='registry_paused')throw err;pausedSkills.add(id);registryReview=null;}}
function pumpPictures(){while(picturesRunning<4&&pictureQueue.length){picturesRunning++;pictureQueue.shift()();}}
function iconSha(e){return e.icon&&typeof e.icon.sha256==='string'&&/^[a-f0-9]{64}$/.test(e.icon.sha256)?e.icon.sha256:'';}
// Only the Browse button calls this, so typing a search never sends a request: each listed icon is asked for once.
function loadPictures(entries){
 pictures.clear();pictureQueue.length=0;
 entries.forEach(e=>{const sha=iconSha(e),k=e.id+' '+sha;if(!sha||pictures.has(k))return;
  pictures.set(k,new Promise(resolve=>{pictureQueue.push(()=>post({action:'registry_picture',id:e.id,sha256:sha,approved:true}).then(r=>'data:image/png;base64,'+r.data).catch(()=>'').then(url=>{picturesRunning--;pumpPictures();resolve(url);}));}));});
 pumpPictures();
}
function skillPicture(e){
 const tile=node('span',undefined,'addon-icon skill-pic'),missing=()=>{tile.classList.remove('skill-loading');tile.classList.add('skill-missing');tile.innerHTML=MISSING_PICTURE;};
 const loading=iconSha(e)&&pictures.get(e.id+' '+iconSha(e));
 if(!loading){missing();return tile;}
 tile.classList.add('skill-loading');  // an empty tile while the picture is checked, never "no picture" first
 loading.then(url=>{if(!url)return missing();const img=document.createElement('img');img.alt='';img.decoding='async';
  img.addEventListener('error',missing);img.src=url;tile.classList.remove('skill-loading');tile.append(img);});
 return tile;
}
// Reporting happens on the Skill Codex website, where the person signs in, picks a reason and sends it; VOOL only opens the page.
function reportAnchor(e){
 const page=registryData&&registryData.report_page;
 if(typeof page!=='string'||!page.startsWith('https://'))return null;
 const label=tr('registry_report','Report this skill'),a=link(label,page+'?id='+encodeURIComponent(e.id));
 a.setAttribute('aria-label',label+': '+(e.title||e.id));return a;
}
function reportRow(e){const row=node('div',undefined,'skill-report-row'),a=reportAnchor(e);if(a)row.append(a);return row;}
function reportBlock(e){
 const box=node('div',undefined,'skill-report'),a=reportAnchor(e);if(!a)return box;
 const line=node('p',undefined,'addon-meta');line.append(a);
 box.append(line,node('p',tr('registry_report_note',"Opens the Skill Codex website in your browser. Sign in there, pick a reason and press Send report. VOOL doesn't send anything itself."),'addon-meta'));
 return box;
}
function registryFacts(e){
 // Author-declared languages and needs, and the builds the signed entry was tested on.
 const needs={search_web:tr('registry_need_search_web','Web search'),llm_cloud:tr('registry_need_llm_cloud','Cloud AI model'),chain_data:tr('registry_need_chain_data','Chain data'),solana_rpc:tr('registry_need_solana_rpc','Solana connection'),wallet:tr('registry_need_wallet','VOOL wallet'),browser:tr('registry_need_browser','Browser'),files:tr('registry_need_files','Your files')};
 const platforms={'macos-arm64':'macOS Apple Silicon','macos-x86_64':'macOS Intel','linux-x86_64':'Linux','windows-x86_64':'Windows'};
 const facts=node('dl',undefined,'addon-facts');
 const add=(label,value)=>{if(value){facts.append(node('dt',label),node('dd',value));}};
 if(Array.isArray(e.languages))add(tr('registry_languages','Languages'),e.languages[0]==='any'?tr('registry_any_language','Any language'):e.languages.join(', '));
 if(Array.isArray(e.needs))add(tr('registry_needs','Needs'),e.needs.length?e.needs.map(id=>needs[id]||id).join(', '):tr('registry_needs_none','Nothing outside VOOL'));
 const builds={};(e.targets||[]).forEach(t=>{(builds[t.version]=builds[t.version]||[]).push(platforms[t.platform]||t.platform);});
 add(tr('registry_tested','Tested with'),Object.entries(builds).map(([version,list])=>'VOOL '+version+' ('+list.join(', ')+')').join('; '));
 return facts;
}
function renderRegistry(host,query){
 hero(host,tr('registry_title','Skills and plugins from their creators'),tr('registry_intro','Made by their creators. VOOL does not write or vouch for community skills, and automated checks can miss things. Download for inspection, then choose whether to install and enable it.'));
 if(registryReview){
  const r=registryReview,info=r.metadata;
  host.append(node('h3',(info.title?info.title+' · ':'')+info.id+' · '+info.version),node('p',info.summary),registryFacts({...info,targets:r.targets}));nativeControlNotice(host,r);
  const details=node('details');details.append(node('summary',tr('registry_evidence','Exact files, permissions and source')),
   node('pre',JSON.stringify({sha256:r.sha256,source:info.source,permissions:r.permissions,tools:r.tools,credential_bindings:r.credential_bindings,dependencies:r.dependencies,files:r.files},null,2)));host.append(details);
  Object.entries(r.instructions||{}).forEach(([name,body])=>{const item=node('details');item.append(node('summary',name),node('pre',body));host.append(item);});
  const acts=node('div',undefined,'addon-actions');host.append(acts);
  const accept=enabled=>action(()=>unlessPaused(r.id,async()=>{await post({action:'registry_install',id:r.id,version:r.version,review_id:r.review_id,accepted:true,enable:enabled});registryReview=null;tab='installed';_pluginCatalog=await(await fetch('/api/plugins')).json();data=await read();}));
  acts.append(button(tr('registry_install_only','Install without enabling'),()=>accept(false)),button(tr('registry_enable','Install and enable'),()=>accept(true),'addon-primary'),button(tr('cancel','Cancel'),()=>{registryReview=null;refresh();}));
  host.append(reportBlock(info));return;
 }
 host.append(button(busy?tr('loading','Loading…'):tr('registry_refresh','Browse Skill Codex'),()=>action(async()=>{registryData=await post({action:'registry_refresh',approved:true});pausedSkills.clear();loadPictures(registryData.entries);_pluginCatalog=await(await fetch('/api/plugins')).json();}),'addon-primary'));
 host.append(node('p',tr('registry_network','This button requests the public registry. No account, chats, local files or provider keys are sent.'),'addon-meta'));
 if(!registryData)return;
 const rows=registryData.entries.filter(e=>key([e.id,e.summary,e.category,e.subcategory,catLabel(e)].join(' ')).includes(key(query)));
 if(!rows.length)host.append(node('p',tr('registry_empty','No listed versions match this exact VOOL build and search.')));
 const grid=node('div',undefined,'addon-grid');host.append(grid);
 rows.forEach((e,i)=>{const card=node('article',undefined,'addon-card');const head=node('div',undefined,'addon-card-head'),name=node('h3',e.title||e.id);name.id='registry-skill-'+i;card.setAttribute('aria-labelledby',name.id);head.append(skillPicture(e),name);card.append(head,node('p',e.summary),node('small',(e.title?e.id+' · ':'')+catLabel(e)+' · '+e.version+' · '+e.license),registryFacts(e));
  if(pausedSkills.has(e.id)){const note=node('p',PAUSED_NOTE(),'addon-meta skill-paused');note.setAttribute('role','status');card.append(note);}
  else card.append(button(tr('registry_inspect','Download for inspection'),()=>action(()=>unlessPaused(e.id,async()=>{registryReview=await post({action:'registry_prepare',id:e.id,version:e.version,approved:true});})),'addon-primary'));
  card.append(reportRow(e));grid.append(card);});
}
function renderLibrary(host,query){
 // Keep storage recovery, first-party identity and lifecycle controls at their existing owners.
 const d=typeof _pluginCatalog!=='undefined'?_pluginCatalog:{};
 if(_pluginMode==='skills')renderSkills(d,query);else renderPlugins(d,query);
 const grid=node('div',undefined,'addon-library-grid');while(host.firstChild)grid.append(host.firstChild);host.append(grid);
 const intro=node('div');hero(intro,tr('library_title','Your add-ons'),tr('library_intro','Everything installed in VOOL. Turn a plugin off without removing its files.'));
 const types=node('div',undefined,'addon-controls');[['plugins',tr('plugins','Plugins')],['skills',tr('skills','Skills')]].forEach(([id,label])=>{const b=button(label,()=>{_pluginMode=id;refresh();});b.setAttribute('aria-pressed',String(_pluginMode===id));types.append(b);});intro.append(types);(data?.entries||[]).filter(e=>e.installed&&e.scan==='risks_accepted').forEach(e=>{const note=node('p',e.name+' · '+tr('risk_accepted','Risks accepted by you'),'addon-risk-note');note.append(button(tr('view_decision','View decision'),()=>{tab='discover';selected=e;refresh();},'addon-linkbtn'));intro.append(note);});host.prepend(intro);
}
// Publishing a skill the person made: VOOL checks it on this device, then only opens the Skill Codex
// website with the skill filled in, the way "Report this skill" opens its page. VOOL sends nothing itself.
const fill=(text,params)=>Object.keys(params).reduce((t,k)=>t.split('{'+k+'}').join(String(params[k])),text);
function publishReason(r){
 // A reason VOOL has no translation for keeps the Skill Codex's own English words, marked as English.
 const li=node('li');const said=node('span',r.code==='codex_check'?String(r.text||''):fill(tr('publish_reason_'+r.code,r.text),{tools:r.tools||''}));
 if(r.code==='codex_check'){said.lang='en';said.dir='ltr';}li.append(said);
 if(Array.isArray(r.files)&&r.files.length)li.append(node('small',r.files.join(', ')));
 if(Number.isInteger(r.line))li.append(node('small',fill(tr('publish_line','In line {line}'),{line:r.line})));
 if(typeof r.quote==='string'&&r.quote){const q=node('code',r.quote,'skill-publish-quote');q.dir='auto';li.append(q);}
 return li;
}
// Where focus goes after the next render: the open panel's heading, or the Publish button it came from.
let publishFocus=null;
function publishPanel(result){
 const box=node('section',undefined,'skill-publish');box.setAttribute('aria-live','polite');
 const close=button(tr('publish_close','Close'),()=>{publishFocus={slug:publishState.slug,to:'button'};publishState=null;refresh();},'skill-publish-quiet');
 const heading=text=>{const h=node('h4',text);h.tabIndex=-1;return h;};
 if(result.status!=='ok'){
  box.classList.add('refused');box.append(heading(tr('publish_refused','Not ready for the Skill Codex yet')));
  const list=node('ul');(result.reasons||[]).forEach(r=>list.append(publishReason(r)));
  box.append(list,node('p',tr('publish_fix_hint','To fix it, ask VOOL in a chat to rewrite this skill, then press Publish to Skill Codex again.'),'addon-meta'),close);return box;
 }
 box.append(heading(tr('publish_ready','Ready to send to the Skill Codex')),node('p',tr('publish_ready_note','The Skill Codex checks it again before it is published.'),'addon-meta'));
 const facts=node('dl',undefined,'addon-facts');facts.append(node('dt',tr('publish_name','Skill name')),node('dd',result.title));box.append(facts);
 box.append(node('p',tr('publish_goes_along','Only this skill goes along: its name, description and text. No chats, memory or keys.'),'addon-meta'));
 const shown=!!publishState.showText;
 const toggle=button(shown?tr('publish_hide_text','Hide text'):tr('publish_show_text','Show text'),()=>{publishState.showText=!shown;refresh();},'skill-publish-quiet');
 toggle.setAttribute('aria-expanded',String(shown));box.append(toggle);
 if(shown){const pre=node('pre',result.text,'skill-publish-text');pre.dir='auto';box.append(pre);}
 box.append(node('p',tr('publish_note','Open Skill Codex opens the website in your browser with the skill filled in. Sign in, pick a category, agree that anyone may use, copy, change and share it (MIT licence), and press Send. VOOL sends nothing itself.'),'addon-meta'));
 const open=link(tr('publish_open','Open Skill Codex'),result.url);open.className='addon-primary skill-publish-open';
 const acts=node('div',undefined,'addon-actions');acts.append(open,close);box.append(acts);return box;
}
function publishControl(s){
 const wrap=node('div'),row=node('div',undefined,'skill-publish-row'),label=tr('publish_button','Publish to Skill Codex');
 const open=publishState&&publishState.slug===s.slug?publishState:null;
 const b=button(busy&&open&&!open.data?tr('checking','Checking…'):label,()=>{if(busy)return;publishState={slug:s.slug,data:null,showText:false};
  action(async()=>{let data;
   // Whatever goes wrong, the person sees it in this skill's panel: a failed check never looks like nothing happened.
   try{data=await post({action:'skill_publish',name:s.slug});}catch(e){data={status:'refused',reasons:[{code:'check_failed',text:tr('publish_reason_check_failed',"VOOL couldn't finish checking this skill. Nothing was sent.")}]};}
   if(publishState&&publishState.slug===s.slug){publishState.data=data;publishFocus={slug:s.slug,to:'panel'};}});},'skill-publish-btn');
 b.setAttribute('aria-label',label+': '+s.name);row.append(b);wrap.append(row);
 let target=b;
 if(open&&open.data){const panel=publishPanel(open.data);wrap.append(panel);if(publishFocus&&publishFocus.to==='panel')target=panel.querySelector('h4');}
 if(publishFocus&&publishFocus.slug===s.slug){publishFocus=null;setTimeout(()=>{if(target.isConnected)target.focus();},0);}
 return wrap;
}
function detailHead(host,e){const head=node('div',undefined,'addon-detail-head');const title=node('div');const byline=node('p');byline.append(brand(e.ecosystem||e.publisher),document.createTextNode(' · '+e.category));title.append(node('h2',listingTitle(e)),byline);head.append(icon(e),title);host.append(head);}
function technical(host,e){const details=node('details');details.append(node('summary',tr('source_details','Source & technical details')),node('p',e.requirements),node('p',e.compatibility),node('p',tr('revision','Pinned revision:')+' '+e.ref,'addon-meta'),link(tr('source','View source'),e.repository+'/tree/'+e.ref+'/'+e.path));host.append(details);}
async function remoteFind(){
 const query=document.getElementById('pluginsSearch')?.value.trim()||'';
 if(!query){error=tr('enter_search','Enter a name to search, or paste a GitHub repository URL.');refresh();return;}
 await action(async()=>{selected=null;review=null;remote=/^(https:\/\/github\.com\/|[\w.-]+\/[\w.-]+$)/.test(query)?await post({action:'github_repository',repository:query}):await post({action:'github_search',query});});
}
function renderRemote(host){
 hero(host,remote.repository||tr('github_results','GitHub results'),remote.repository?tr('repo_skills','Skills found in this repository. Select one to inspect its exact version.'):tr('repo_results','Public repositories matching your search. Open one to find its skills; compatibility is checked separately.'));
 const grid=node('div',undefined,'addon-grid');host.append(grid);
 if(remote.repositories){
  if(!remote.repositories.length)grid.append(node('p',tr('no_repos','No repositories found. Try a different name or paste the repository URL.')));
  remote.repositories.forEach(r=>{const card=node('article',undefined,'addon-card');card.append(node('h3',r.name),node('p',r.description),node('span',r.license+' · '+r.stars+' '+tr('github_stars','GitHub stars'),'addon-meta'),button(tr('explore_skills','Explore skills'),()=>action(async()=>{remote=await post({action:'github_repository',repository:r.repository});}),'addon-primary'));grid.append(card);});
 }else{
  if(!remote.entries.length)grid.append(node('p',tr('no_skill_files','No SKILL.md files found. This may be an executable plugin or another format that needs a VOOL adapter.')));
  remote.entries.forEach(e=>{const card=node('article',undefined,'addon-card');card.append(node('h3',e.name),node('p',e.skill_path),button(tr('view','View add-on'),()=>{selected=e;refresh();},'addon-primary'));grid.append(card);});
 }
}
function localFindings(report){return Array.isArray(report&&report.findings)?report.findings:[];}
function renderLocalCheck(host,e){
 // A VOOL-attributed report beside the Eyebrow one; it never replaces or overwrites it.
 const box=node('section',undefined,'addon-localcheck');host.append(box);
 box.append(node('h3',tr('local_check_title','VOOL local check')));
 box.append(node('p',e.bundled_source?tr('local_check_offline','Runs on this device. No API key, no scan allowance, and nothing leaves your machine.'):tr('local_check_download','Downloads the pinned public file first, then checks it on this device. The check itself is local and uses no key or allowance.'),'addon-meta'));
 const body=node('div');box.append(body);
 const paint=state=>{
  body.replaceChildren();
  if(state&&state.state==='current'){
   const r=state.local_check||{};const fs=localFindings(r);
   if(r.status==='findings'&&fs.length)body.append(node('p',tr('local_check_found','Local findings (advisory):')+' '+fs.length+' · '+tr('local_check_highest','highest severity')+' '+String(fs[0]?fs[0].severity:'').toUpperCase()));
   else if(r.status==='clean')body.append(node('p',tr('local_check_clean','No findings in the local check. This is not a safety guarantee.')));
   else body.append(node('p',tr('local_check_status','Local check status:')+' '+String(r.status||'unknown')+(r.reason?' ('+r.reason+')':'')+' · '+tr('local_check_incomplete','not a clean pass')));
   if(fs.length){const list=node('ul',undefined,'addon-findings');fs.forEach(f=>{const li=node('li');const stanceLabel=f.stance?tr('local_stance_'+f.stance,''):'';const where=f.context?[f.context.section,f.context.block].filter(Boolean).join(' · '):'';li.append(node('strong','VOOL local · '+String(f.severity||'').toUpperCase()+' · '+f.ruleId+' · '+tr('local_confidence','confidence')+' '+f.confidence));if(stanceLabel||where)li.append(node('p',[where,stanceLabel].filter(Boolean).join(' — '),'addon-meta'));li.append(node('span',f.explanation||''),node('small',[f.line?tr('line','Line')+' '+f.line:'',f.snippet||''].filter(Boolean).join(' · ')));const why=node('details');why.append(node('summary',tr('local_why','Why was this flagged?')));(f.limitations||[]).forEach(t=>why.append(node('p',t)));why.append(node('p',tr('local_limits','A finding is not proof of malicious intent. This static check cannot read intent or predict runtime behaviour.'),'addon-meta'));li.append(why);list.append(li);});body.append(list);}
   body.append(node('p',tr('local_check_advisory','Advisory only: it does not replace the Eyebrow scan, the installation checks, or your decision.'),'addon-meta'));
   const evidence=node('details');evidence.append(node('summary',tr('local_report','View local report')),node('pre',JSON.stringify({scanner:r.scanner,scanner_version:r.scanner_version,ruleset_version:r.ruleset_version,status:r.status,reason:r.reason,source:r.source,findings:r.findings,limitations:r.limitations,duration_ms:r.duration_ms},null,2)));body.append(evidence);
   body.append(button(tr('local_rerun','Run local check again'),()=>action(async()=>{localCheck={id:e.id,data:await post({action:'local_check',id:e.id})};paint(localCheck.data);})));
  }else{
   if(state&&state.state==='stale')body.append(node('p',state.message||tr('local_stale','The source changed since this local check. Run it again.'),'addon-error'));
   else if(state&&state.state==='none')body.append(node('p',tr('local_not_run','Not run yet for this version.'),'addon-meta'));
   body.append(button(busy?tr('local_running','Checking…'):tr('local_run','Run local check'),()=>action(async()=>{localCheck={id:e.id,data:await post({action:'local_check',id:e.id})};paint(localCheck.data);})));
  }
 };
 if(localCheck&&localCheck.id===e.id&&localCheck.data){paint(localCheck.data);}
 else{paint(null);post({action:'local_report',id:e.id}).then(state=>{if(!body.isConnected)return;if(!localCheck||localCheck.id!==e.id||!localCheck.data)localCheck={id:e.id,data:state};paint(state);}).catch(()=>{if(body.isConnected)paint({state:'none'});});}
}
function renderDetail(host,e){
 const detail=node('section',undefined,'addon-detail');host.append(detail);detailHead(detail,e);detail.append(node('p',e.description));
 const facts=node('dl',undefined,'addon-facts');[[tr('type','Type'),tr('instruction_skill','Instruction skill')],[tr('license','License'),e.license],[tr('access','Added access'),tr('none','None')]].forEach(([k,v])=>{const f=node('div');f.append(node('dt',k),node('dd',v));facts.append(f);});detail.append(facts);
 if(e.compatibility)detail.append(node('p',e.compatibility,'addon-meta'));
 if(e.scan==='risks_accepted')detail.append(node('p',tr('risk_accepted','Risks accepted by you')+' · '+tr('eyebrow_verdict','Eyebrow verdict:')+' '+e.scan_verdict+' · '+new Date(e.acceptance.accepted_at*1000).toLocaleString(),'addon-risk-note'));

 detail.append(node('h3',tr('how_it_works','How it works')),node('p',tr('how_skill_works','Adds guidance your model can use for relevant tasks. It does not install programs or grant access to your files or accounts.')));
 const acts=node('div',undefined,'addon-actions');detail.append(acts);
 if(e.discovery_only){acts.append(button(busy?tr('checking','Checking…'):tr('check_compatibility','Check compatibility'),()=>action(async()=>{const out=await post({action:'github_inspect',repository:e.repository,ref:e.ref,path:e.skill_path});selected=out.entry;data=await read();}),'addon-primary'));detail.append(node('p',tr('compat_privacy','Reads this pinned public skill and its licence from GitHub. No scan, installation or execution yet.'),'addon-meta'));}
 else if(e.import_ready===false){acts.append(node('p',tr('not_supported','Not supported yet'),'addon-error'));}
 else if(e.installed){acts.append(button(tr('manage','Manage in My add-ons'),()=>{tab='installed';selected=null;_pluginMode='plugins';refresh();}));}
 else{const saved=(data.reviews||[]).find(r=>r.entry.id===e.id);if(saved)acts.append(button(tr('resume_review','Review scan'),()=>action(async()=>{review=await post({action:'review',review_id:saved.review_id});selected=null;}),'addon-primary'));
 acts.append(button(e.bundled_source?tr('scan_saved','Scan with Eyebrow'):tr('scan','Download and scan'),()=>{if(!data.eyebrow.configured){tab='security';selected=null;}else{review={entry:e,consent:true};selected=null;}refresh();},saved?'':'addon-primary'));}
 if(!e.installed&&!e.discovery_only&&e.import_ready!==false)renderLocalCheck(detail,e);
 detail.append(node('p',tr('review_before_install','You review the scan before installing. Nothing runs when you browse.'),'addon-meta'));technical(detail,e);
}
function riskExplanation(ruleId){
 const d=node('details',undefined,'addon-risk-explanation');
 d.append(node('summary',tr('risk_explanation','What could go wrong?')),node('p',tr('risk_explanation_source','VOOL explanation · possible outcomes, not a prediction'),'addon-meta'));
 const rule=String(ruleId||'').trim().toUpperCase();
 if(rule==='PROMPT-INJECTION'){
  d.append(node('p',tr('risk_injection','These instructions may try to make VOOL ignore your request, hide what it is doing, or ask for access it does not need.')),
   node('p',tr('risk_injection_worst','Worst case: if the required access is already allowed or you approve it, the skill could steer VOOL into sharing private information, changing or deleting files, or sending unwanted messages.')));
 }else if(rule==='SENSITIVE-PATH-READ'){
  d.append(node('p',tr('risk_sensitive','This skill refers to files that may contain passwords, API keys or other private information.')),
   node('p',tr('risk_sensitive_worst','Worst case: if VOOL is allowed to read and share those files, secrets could leave your device and someone could use them to access your accounts.')));
 }else{
  d.append(node('p',tr('risk_unknown','Eyebrow flagged a possible risk. VOOL has no specific explanation for this finding yet; the report alone cannot tell us what would happen.')),
   node('p',tr('risk_unknown_worst','Depending on the instructions and allowed access, harm could include private data being shared or unwanted changes. Review the finding and source before deciding.')));
 }
 d.append(node('p',tr('risk_limits','A warning is not proof of malicious intent; scanners can make mistakes. Enabling a skill adds instructions, not permissions. Existing permissions still apply.'),'addon-meta'));
 return d;
}
function renderReview(host){
 const e=review.entry;const detail=node('section',undefined,'addon-detail');host.append(detail);detailHead(detail,e);
 const box=node('section',undefined,'addon-review'+(!review.consent&&!review.can_install?' blocked':''));box.setAttribute('aria-live','polite');detail.append(box);
 if(review.consent){box.append(node('h3',tr('before_scan','One check before you install')),node('p',e.bundled_source?tr('scan_bundled_consent','Send this included skill to Eyebrow for one scan. No GitHub download is needed. No private files, chats or other keys are included. Nothing is installed or executed.'):tr('scan_consent','Download this public skill from GitHub and send its contents to Eyebrow for one scan. No private files, chats or keys are included. Nothing is installed or executed.')),node('p',tr('scan_allowance','Uses one scan from your Eyebrow allowance.'),'addon-meta'));const acts=node('div',undefined,'addon-actions');box.append(acts);
 acts.append(button(busy?tr('scanning','Scanning…'):tr('confirm_scan','Run one scan'),()=>action(async()=>{review=await post({action:'scan',id:e.id,approved:true});data=await read();}),'addon-primary'),button(tr('cancel','Cancel'),()=>{review=null;selected=e;refresh();}));return;}
 const r=review.report||{};const findings=Array.isArray(r.findings)?r.findings:[];
 box.append(node('h3',tr('eyebrow_report','Eyebrow security report')),node('p',tr('eyebrow_verdict','Eyebrow verdict:')+' '+String(r.verdict||tr('unknown','Unknown')).toUpperCase()));
 if(!findings.length)box.append(node('p',tr('no_eyebrow_findings','No findings listed in this report. This is not a guarantee of safety.')));
 if(findings.length){const list=node('ul',undefined,'addon-findings');findings.forEach(f=>{const li=node('li');li.append(node('strong','Eyebrow · '+(f.severity||'').toUpperCase()+' · '+f.ruleId),node('span',f.explanation||f.message||f.description||f.ruleId),node('small',[f.file,f.line?tr('line','Line')+' '+f.line:''].filter(Boolean).join(' · ')),riskExplanation(f.ruleId));list.append(li);});box.append(list);}
 const violations=Array.isArray(r.policy?.violations)?r.policy.violations:[];
 if(!findings.length&&(violations.length||String(r.verdict).toLowerCase()==='fail'))box.append(riskExplanation(''));
 if(violations.length){box.append(node('h3',tr('eyebrow_policy','Eyebrow policy results')));const list=node('ul');violations.forEach(v=>list.append(node('li',typeof v==='string'?v:v?.message||v?.reason||v?.ruleId||v?.kind||JSON.stringify(v))));box.append(list);}
 box.append(node('p',tr('eyebrow_engine','Eyebrow engine:')+' '+r.engine+' · '+new Date(review.checked_at*1000).toLocaleString(),'addon-meta'));
 // Both scanner summaries sit above the installation decision buttons, so the
 // decision is made with the local findings visible; the local check stays
 // optional and advisory, and neither scanner can hide the other.
 renderLocalCheck(detail,e);
 const gate=node('section',undefined,'addon-review'+(review.vool_check?.status==='blocked'?' blocked':''));
 gate.append(node('h3',tr('vool_checks','VOOL installation checks')),node('p',review.vool_check?.message||review.reason||tr('review_again','Reopen this scan to check its installation status.')),node('p',tr('separate_decisions','Eyebrow supplies the scan findings. VOOL checks package compatibility and file integrity, then applies your installation decision.'),'addon-meta'));detail.append(gate);
 const acts=node('div',undefined,'addon-actions');gate.append(acts);
 if(review.can_install)acts.append(button(tr('install','Accept and enable'),()=>installDecision(false),'addon-primary'));
 acts.append(button(tr('reject','Reject'),()=>action(async()=>{await post({action:'reject',review_id:review.review_id});review=null;data=await read();})));
 if(review.can_override){
  const advanced=node('details',undefined,'addon-override');advanced.append(node('summary',tr('advanced_install','Advanced: install despite findings')));
  advanced.append(node('p',tr('override_scope','These instructions could mislead your model or request sensitive actions. Accepting the risks enables this exact scanned version only. It does not mark the scan as passed or change VOOL permissions.')));
  const label=node('label');const check=node('input');check.type='checkbox';check.disabled=busy;label.append(check,node('span',tr('risk_ack','I reviewed the Eyebrow findings and accept the risks of enabling this exact version.')));advanced.append(label);
  const confirm=button(tr('install_with_risks','Accept risks and enable this version'),()=>{if(check.checked)installDecision(true);},'addon-risk-button');confirm.disabled=true;
  check.onchange=()=>{confirm.disabled=busy||!check.checked;};advanced.append(confirm);gate.append(advanced);
 }
 detail.append(node('p',tr('permissions','This instruction pack adds no executable tools or permission grants. Existing VOOL tools still require their normal permissions.'),'addon-meta'));
 const evidence=node('details');evidence.append(node('summary',tr('report','View scan report')),node('pre',JSON.stringify({scanner:'Eyebrow',engine:r.engine,verdict:r.verdict,findings:r.findings,policy:r.policy,artifacts:r.artifacts,lockfile:r.lockfile,quota_remaining:r.quota_remaining,vool_installation_checks:review.vool_check},null,2)));detail.append(evidence);technical(detail,e);
}
function installDecision(risks){
 const id=review.review_id;return action(async()=>{await post({action:'install',review_id:id,accepted:true,...(risks?{risk_override:true,risk_acknowledged:true}:{})});review=null;localCheck=null;data=await read();_pluginCatalog=await (await fetch('/api/plugins')).json();});
}
window.VoolAddons={open:()=>{nav();return load();},render,mountSecurity:security,publishControl};
})();</script>
'''
