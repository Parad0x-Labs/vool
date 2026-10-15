"""core/school/pages.py — VOOL School surfaces (server-rendered, no framework).

Two pages:
  /school           the console (login → SCHOOL_ADMIN or TEACHER views)
  /school/student   the student shell (banner, chat, assignment, hand in)

Same visual language as the rest of VOOL: inline CSS/JS, no CDN, no build step.
The pages own no state — every control names a /school/api/* authority.
"""

from __future__ import annotations

import html
from typing import Any

_BASE_CSS = """
:root{--bg:#0f1115;--panel:#171a21;--line:#262b36;--txt:#e8eaf0;--dim:#9aa3b2;--acc:#c3b8a8;--acc-ink:#171513;--ok:#3fbf7f;--warn:#e0a83c;--bad:#e0605c}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
a{color:var(--acc)}
.wrap{max-width:1060px;margin:0 auto;padding:24px 18px 80px}
h1{font-size:20px;margin:0 0 4px} h2{font-size:15px;margin:22px 0 8px;color:var(--dim);text-transform:uppercase;letter-spacing:.06em}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px;margin:10px 0}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
input,select,textarea,button{font:inherit;background:#10131a;color:var(--txt);border:1px solid var(--line);border-radius:8px;padding:8px 10px}
textarea{width:100%;min-height:90px;resize:vertical}
button{cursor:pointer;background:#1c2740;border-color:#31405f}
button.primary{background:var(--acc);border-color:var(--acc);color:var(--acc-ink);font-weight:600}
button.danger{background:#4a2525;border-color:#6e3535}
.chip{display:inline-block;background:#1c2740;border:1px solid #31405f;border-radius:999px;padding:2px 10px;font-size:12px;margin:2px 4px 2px 0}
.chip.on{border-color:var(--ok);color:var(--ok)} .chip.off{border-color:var(--bad);color:var(--bad)} .chip.warn{border-color:var(--warn);color:var(--warn)}
table{width:100%;border-collapse:collapse;margin:8px 0}
td,th{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;font-size:13px}
.dim{color:var(--dim);font-size:12px}
.code{font-family:ui-monospace,monospace;background:#10131a;border:1px solid var(--line);padding:2px 6px;border-radius:6px}
.msg{border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin:6px 0;white-space:pre-wrap}
.msg.user{background:#1a2233}
.msg.bot{background:#151a14}
.banner{position:sticky;top:0;z-index:5;background:linear-gradient(90deg,#141b2e,#101724);border-bottom:1px solid var(--line);padding:10px 18px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.hidden{display:none}
#chatlog{max-height:46vh;overflow-y:auto;padding:6px 2px}
.quota{font-size:12px;color:var(--dim)}
label{font-size:12px;color:var(--dim)}
.say{min-height:20px;font-size:13px;margin-top:8px}
.say.bad{color:var(--bad)}
.secret{font-family:ui-monospace,monospace;font-size:22px;letter-spacing:2px;text-align:center;padding:14px;border:1px solid var(--warn);border-radius:10px;background:#10131a;margin:10px 0;overflow-wrap:anywhere;user-select:all}
"""

_JS_HELPERS = """
// Server answers carry short codes; people see a sentence. An unknown code gets a calm retry line.
const SAY = {
  invalid_or_used_code: "That code didn't work or was already used. Ask your teacher or school admin for a new one.",
  school_session_required: 'Sign in first, then try again.',
  admin_only: 'Only a school admin can do that.',
  admin_only_or_bad_role: 'Only a school admin can do that, and the role must be one of the listed ones.',
  admin_only_or_bad_teacher: 'Only a school admin can do that, with a teacher from the list.',
  admin_only_or_bad_target: 'Only a school admin can do that.',
  not_your_class: 'That class is not one of yours.',
  not_your_class_or_lesson_inactive: 'That class is not yours, or its lesson has ended.',
  no_active_lesson: 'There is no lesson running right now.',
  teacher_or_admin_only: 'Only a teacher or a school admin can do that.',
  not_found: 'That item no longer exists. Reload the page.',
};
const OFFLINE = "VOOL isn't answering right now. Check that it is running, then try again.";
function plain(d, status){
  const raw = String((d && (d.detail || d.error)) || '');
  if (SAY[raw]) return SAY[raw];
  if (/\\s/.test(raw)) return raw;
  return 'Something went wrong. Please try again.';
}
async function api(path, body, sayId){
  let r;
  try { r = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify(body||{})}); }
  catch (e) { tell(OFFLINE, sayId); throw {status:0, data:{}}; }
  const d = await r.json().catch(()=>({}));
  if(!r.ok){ if (r.status !== 409 || !d.already_exists) tell(plain(d, r.status), sayId); throw {status:r.status, data:d}; }
  if (sayId) tell('', sayId);
  return d;
}
async function get(path){
  const r = await fetch(path); const d = await r.json().catch(()=>({}));
  if(!r.ok) throw {status:r.status, data:d}; return d;
}
// A message next to the field it is about when there is one, else the corner note.
function tell(text, sayId){
  const box = sayId && document.getElementById(sayId);
  if (box) { box.textContent = text; box.className = 'say' + (text ? ' bad' : ''); return; }
  if (text) toast(text);
}
function toast(text){ const t=document.getElementById('toast'); t.textContent=text; t.classList.remove('hidden');
  setTimeout(()=>t.classList.add('hidden'), 6000); }
// Names, titles and notes are typed by people: they go into markup only escaped. Ids minted by the
// server also land inside onclick handlers, so they are reduced to the characters ids are made of.
function esc(v){ return String(v==null?'':v).replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function sid(v){ return String(v==null?'':v).replace(/[^A-Za-z0-9_.:-]/g,''); }
function el(id){ return document.getElementById(id); }
function show(id, on){ el(id).classList.toggle('hidden', !on); }
"""


def _page(title: str, body: str, script: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>{_BASE_CSS}</style></head>
<body><div id="toast" class="chip warn hidden" style="position:fixed;bottom:18px;right:18px;z-index:99"></div>
{body}<script>{_JS_HELPERS}{script}</script></body></html>"""


# ---------------------------------------------------------------- console ----


def render_console_page(session: Any) -> str:
    if session is None:
        return _login_page()
    if session.role == "SCHOOL_ADMIN":
        return _admin_page(session)
    if session.role == "TEACHER":
        return _teacher_page(session)
    return _student_redirect_note()


def _login_page() -> str:
    body = """
<div class="wrap" style="max-width:440px;margin-top:12vh">
  <h1>VOOL School</h1><div class="dim">Sign in with your school access code.</div>
  <form class="card" id="loginForm" onsubmit="login(event)">
    <div class="row"><input id="code" placeholder="Access code" aria-label="Access code" style="flex:1" autocomplete="off"></div>
    <div class="row" style="margin-top:10px"><button class="primary" type="submit">Sign in</button></div>
    <div class="say" id="loginSay" role="status" aria-live="polite"></div>
    <div class="dim" style="margin-top:10px">First time here? <a href="#" onclick="return bootstrapAsk()">Create the school</a> (this makes the first admin code).</div>
  </form>
  <form class="card hidden" id="createForm" onsubmit="createSchool(event)">
    <label for="schoolName">School name</label>
    <div class="row" style="margin-top:6px"><input id="schoolName" style="flex:1" maxlength="120" autocomplete="off" placeholder="For example Hillside Primary"></div>
    <div class="row" style="margin-top:10px"><button class="primary" type="submit" id="createBtn">Create the school</button>
      <button type="button" onclick="show('createForm',false)">Cancel</button></div>
    <div class="say" id="createSay" role="status" aria-live="polite"></div>
  </form>
  <div class="card hidden" id="adminCodeBox" role="region" aria-labelledby="adminCodeTitle">
    <b id="adminCodeTitle">Your admin access code</b>
    <div class="dim">This is the only time it is shown. Save it somewhere safe before you continue: without it nobody can manage the school.</div>
    <div class="secret" id="adminCode"></div>
    <div class="row"><button type="button" onclick="copyCode()">Copy code</button><span class="dim" id="copySay" aria-live="polite"></span></div>
    <label style="display:flex;gap:8px;align-items:center;margin-top:12px"><input type="checkbox" id="savedIt" onchange="el('continueBtn').disabled=!this.checked"> I have saved this code</label>
    <div class="row" style="margin-top:10px"><button class="primary" type="button" id="continueBtn" disabled onclick="useCode()">Continue to sign in</button></div>
  </div>
</div>"""
    script = """
async function login(ev){ if(ev) ev.preventDefault();
  const code=el('code').value.trim(); if(!code){ tell('Type your access code first.','loginSay'); return; }
  try { await api('/school/api/login',{access_code:code},'loginSay'); location.reload(); } catch(e) {} }
function bootstrapAsk(){ show('createForm',true); el('schoolName').focus(); return false; }
async function createSchool(ev){ ev.preventDefault();
  const name=el('schoolName').value.trim(); if(!name){ tell('Type the school name first.','createSay'); return; }
  el('createBtn').disabled=true;
  try {
    const d=await api('/school/api/bootstrap',{name:name},'createSay');
    show('createForm',false); show('loginForm',false);
    el('adminCode').textContent=d.admin_access_code; show('adminCodeBox',true); el('savedIt').focus();
  } catch(e) {
    if (e && e.data && e.data.already_exists) tell('This school already exists. Sign in with an admin code.','createSay');
    else if (e && e.status) tell("Couldn't create the school. Please try again.",'createSay');
  } finally { el('createBtn').disabled=false; } }
async function copyCode(){ const code=el('adminCode').textContent;
  try { await navigator.clipboard.writeText(code); el('copySay').textContent='Copied.'; }
  catch(e) { const r=document.createRange(); r.selectNodeContents(el('adminCode')); const s=getSelection(); s.removeAllRanges(); s.addRange(r);
    el('copySay').textContent='Selected. Press Ctrl+C or Cmd+C to copy it.'; } }
function useCode(){ el('code').value=el('adminCode').textContent; el('adminCode').textContent=''; show('adminCodeBox',false); show('loginForm',true); el('code').focus(); }
"""
    return _page("VOOL School — Sign in", body, script)


def _student_redirect_note() -> str:
    body = """<div class="wrap"><div class="card">You are signed in as a student.
    Use the <a href="/school/student">student page</a>.</div></div>"""
    return _page("VOOL School", body, "")


def _admin_page(session) -> str:
    body = f"""
<div class="banner"><b>VOOL School · Admin</b><span class="dim">{html.escape(str(session.display_name))}</span>
  <span style="flex:1"></span><a href="/school/student">student view</a> · <button onclick="logout()">Sign out</button></div>
<div class="wrap">
  <h1 id="schoolname">School</h1>
  <div class="row"><input id="quota_req" type="number" placeholder="requests/day"><input id="quota_tok" type="number" placeholder="tokens/day">
    <button onclick="savePolicy()">Save school limits</button>
    <span class="dim">defaults: 200 requests · 200k tokens per student per day</span></div>

  <h2>People</h2>
  <div class="card"><div class="row">
    <select id="newrole"><option>TEACHER</option><option>STUDENT</option><option>SCHOOL_ADMIN</option></select>
    <input id="newname" placeholder="display name" style="flex:1">
    <button class="primary" onclick="addUser()">Add person</button></div>
    <div id="newcode" class="hidden card" style="border-color:var(--warn)"></div>
    <table id="users"><tr><th>name</th><th>role</th><th>user id</th><th></th></tr></table></div>

  <h2>Classes</h2>
  <div class="card"><div class="row">
    <input id="classname" placeholder="class name"><select id="classteacher"></select>
    <button class="primary" onclick="addClass()">Create class</button></div>
    <div id="classes"></div></div>

  <h2>AI Providers &amp; Models</h2>
  <div class="card"><div class="row">
    <input id="prov" placeholder="provider (ollama / openai / …)" style="flex:1">
    <input id="model" placeholder="model id" style="flex:1">
    <select id="loc"><option value="local">local</option><option value="cloud">cloud</option></select>
    <button class="primary" onclick="addProvider()">Allow model</button></div>
    <div class="dim" style="margin:6px 0">The provider API key is stored once, sealed on this machine; students and teachers never see it.</div>
    <table id="providers"></table></div>

  <h2>Audit</h2><div class="card"><table id="audit"></table></div>
</div>"""
    script = """
let STATE=null;
async function refresh(){ STATE=await get('/school/api/state'); draw(); }
function draw(){
  el('schoolname').textContent=STATE.school.name||'School';
  const u=el('users'); u.innerHTML='<tr><th>name</th><th>role</th><th>user id</th><th></th></tr>';
  (STATE.users||[]).forEach(x=>{ u.insertAdjacentHTML('beforeend',
    `<tr><td>${esc(x.display_name)}</td><td>${esc(x.role)}</td><td class="code">${sid(x.user_id)}</td>`+
    `<td>${x.revoked?'<span class="chip off">revoked</span>':'<button class="danger" onclick="revoke(\\''+sid(x.user_id)+'\\')">revoke</button>'}</td></tr>`); });
  const t=el('classteacher'); t.innerHTML='';
  (STATE.users||[]).filter(x=>x.role==='TEACHER'&&!x.revoked).forEach(x=>t.insertAdjacentHTML('beforeend',`<option value="${sid(x.user_id)}">${esc(x.display_name)}</option>`));
  const c=el('classes'); c.innerHTML='';
  (STATE.classes||[]).forEach(k=>{
    c.insertAdjacentHTML('beforeend',`<div class="card"><b>${esc(k.name)}</b> <span class="dim">${sid(k.class_id)}</span>
      <table><tr><th>student</th><th>id</th></tr>`+
      (STATE._enrolled[k.class_id]||[]).map(s=>`<tr><td>${esc(s.display_name)}</td><td class="code">${sid(s.user_id)}</td></tr>`).join('')+
      `</table><div class="row"><select id="enr_${sid(k.class_id)}"></select><button onclick="enroll('${sid(k.class_id)}')">Enroll student</button></div></div>`);
    const sel=el('enr_'+k.class_id);
    (STATE.users||[]).filter(x=>x.role==='STUDENT'&&!x.revoked).forEach(s=>sel.insertAdjacentHTML('beforeend',`<option value="${sid(s.user_id)}">${esc(s.display_name)}</option>`));
  });
  const p=el('providers'); p.innerHTML='<tr><th>provider</th><th>model</th><th>locality</th><th>enabled</th></tr>';
  (STATE.providers||[]).forEach(x=>p.insertAdjacentHTML('beforeend',`<tr><td>${esc(x.provider_id)}</td><td class="code">${esc(x.model_id)}</td><td>${esc(x.locality)}</td><td>${x.enabled?'✓':'—'}</td></tr>`));
  const a=el('audit'); a.innerHTML='<tr><th>when</th><th>action</th><th>actor</th></tr>';
  (STATE.audit||[]).slice(0,50).forEach(x=>a.insertAdjacentHTML('beforeend',`<tr><td class="dim">${esc(x.ts)}</td><td>${esc(x.action)}</td><td class="code">${esc(x.actor_user_id.slice(0,12))}</td></tr>`));
}
async function addUser(){ const d=await api('/school/api/user.create',{role:el('newrole').value,display_name:el('newname').value});
  show('newcode',true); el('newcode').innerHTML=`Access code for <b>${esc(d.display_name)}</b> (shown once): <span class="code">${esc(d.access_code)}</span>`;
  el('newname').value=''; refresh(); }
async function revoke(id){ await api('/school/api/user.revoke',{user_id:id}).catch(e=>{}); refresh(); }
async function addClass(){ await api('/school/api/class.create',{name:el('classname').value,teacher_user_id:el('classteacher').value}); el('classname').value=''; refresh(); }
async function enroll(cid){ await api('/school/api/enroll',{class_id:cid,student_user_id:el('enr_'+cid).value}); refresh(); }
async function addProvider(){ await api('/school/api/provider.set',{entry:{provider_id:el('prov').value,model_id:el('model').value,locality:el('loc').value,enabled:true}}); refresh(); }
async function savePolicy(){ const policy=Object.assign({},STATE.school_policy);
  if(el('quota_req').value) policy.student_daily_requests=parseInt(el('quota_req').value);
  if(el('quota_tok').value) policy.student_daily_tokens=parseInt(el('quota_tok').value);
  await api('/school/api/policy.set',{policy:policy}); toast('Saved'); refresh(); }
async function logout(){ await api('/school/api/logout',{}); location.reload(); }
refresh();
"""
    return _page("VOOL School · Admin", body, script)


def _teacher_page(session) -> str:
    body = f"""
<div class="banner"><b>VOOL School · Teacher</b><span class="dim">{html.escape(str(session.display_name))}</span>
  <span style="flex:1"></span><a href="/school/student">student view</a> · <button onclick="logout()">Sign out</button></div>
<div class="wrap"><h1>Classes</h1><div id="classes"></div></div>"""
    script = """
let STATE=null;
async function refresh(){ STATE=await get('/school/api/state'); draw(); }
function policyChips(p){ if(!p||!Object.keys(p).length) return '<span class="dim">default policy</span>';
  const fam=(p.tool_families&&p.tool_families.length)?p.tool_families.join(', '):'none';
  return `<span class="chip ${fam.includes('web')?'on':'off'}">web: ${fam.includes('web')?'allowed':'blocked'}</span>`+
   `<span class="chip">help ceiling: L${esc(p.max_assistance==null?'10':p.max_assistance)}</span>`+
   `<span class="chip">${p.locality==='local_only'?'local model only':'cloud allowed'}</span>`+
   (p.assessment?'<span class="chip warn">assessment</span>':''); }
function draw(){
  const c=el('classes'); c.innerHTML='';
  (STATE.teaching||[]).forEach(k=>{
    const l=k.active_lesson;
    c.insertAdjacentHTML('beforeend', `
    <div class="card">
      <div class="row"><b style="font-size:16px">${esc(k.name)}</b>
        ${l?'<span class="chip on">lesson active</span>':'<span class="chip off">no active lesson</span>'}
        <span style="flex:1"></span>
        ${l?`<button class="danger" onclick="endLesson('${sid(l.lesson_id)}')">End lesson</button>`
           :`<button class="primary" onclick="show('start_${sid(k.class_id)}',true)">Start lesson</button>`}</div>
      ${l?`<div class="card"><div class="row">Lesson: <b>${esc(l.title)}</b> ${policyChips(l.policy)}</div>
        <div class="row">Join code: <span class="code">${esc(l.join_code)}</span> <span class="dim">(students: redeem in the student page)</span></div>
        <div class="row"><button onclick="show('asg_${sid(k.class_id)}',true)">Send assignment</button></div>
        <div id="asg_${sid(k.class_id)}" class="card hidden">
          <input id="asgt_${sid(k.class_id)}" placeholder="assignment title" style="width:100%">
          <textarea id="asgb_${sid(k.class_id)}" placeholder="instructions for students"></textarea>
          <div class="row"><label>Assistance ceiling for this assignment (optional)</label>
            <select id="asgl_${sid(k.class_id)}"><option value="">inherit lesson</option>
            ${Array.from({length:11},(_,i)=>`<option value="${i}">L${i}</option>`).join('')}</select>
            <label><input type="checkbox" id="asga_${sid(k.class_id)}"> assessment mode</label></div>
          <div class="row"><button class="primary" onclick="sendAssignment('${sid(k.class_id)}','${sid(l.lesson_id)}')">Send to class</button></div></div>
        <table id="asgs_${sid(k.class_id)}"></table></div>`:''}
      <div id="start_${sid(k.class_id)}" class="card hidden">
        <div class="row"><input id="t_${sid(k.class_id)}" placeholder="lesson title" style="flex:1"></div>
        <div class="row">
          <label><input type="checkbox" id="web_${sid(k.class_id)}" checked> allow web</label>
          <label>AI help ceiling
            <select id="lvl_${sid(k.class_id)}">
              <option value="4">L4 guiding questions</option><option value="2">L2 recall only</option>
              <option value="6">L6 analogous examples</option><option value="8">L8 guided solution</option>
              <option value="10">L10 full help</option></select></label>
          <label><input type="checkbox" id="cloud_${sid(k.class_id)}" checked> allow cloud model</label>
          <label><input type="checkbox" id="assess_${sid(k.class_id)}"> assessment mode</label></div>
        <div class="row"><button class="primary" onclick="startLesson('${sid(k.class_id)}')">Start</button></div></div>
      <table><tr><th>student</th><th>id</th></tr>
        ${(k.students||[]).map(s=>`<tr><td>${esc(s.display_name)}</td><td class="code">${sid(s.user_id)}</td></tr>`).join('')}</table>
      ${k.help_queue&&k.help_queue.length?`<h2>Help queue</h2><table><tr><th>when</th><th>student</th><th>note</th></tr>
        ${k.help_queue.map(h=>`<tr><td class="dim">${esc(h.ts)}</td><td>${esc(h.student)}</td><td>${esc(h.note||'')}</td></tr>`).join('')}</table>`:''}
      <div id="subs_${sid(k.class_id)}"></div>
    </div>`);
    if(l){
      const at=el('asgs_'+k.class_id); at.innerHTML='';
      (k.assignments||[]).forEach(a=>at.insertAdjacentHTML('beforeend',`<tr><td>${esc(a.title)}</td><td class="dim">${esc(a.assignment_id.slice(0,14))}</td></tr>`));
      const st=el('subs_'+k.class_id); st.innerHTML='';
      (k.submissions||[]).forEach(s=>st.insertAdjacentHTML('beforeend',
        `<div class="card"><div class="row"><b>${esc(s.student_display_name)}</b><span class="dim">${esc(s.assignment_title)}</span>
         <span class="chip ${s.status==='acknowledged'?'on':'warn'}">${esc(s.status)}</span>
         <span style="flex:1"></span><button onclick="ack('${sid(s.submission_id)}')">Acknowledge</button></div>
         <div class="dim">assistance: ${s.assistance.turns||0} turns · ${s.assistance.violations||0} refusals to dump answers · answer revealed: ${s.assistance.answer_revealed?'yes':'no'} · help requested: ${s.assistance.help_requested||0}×</div>
         <details><summary class="dim">work</summary><div class="msg bot">${(s.work_text||'').replace(/</g,'&lt;')}</div></details></div>`));
    }
  });
}
async function startLesson(cid){
  const policy={tool_families:['workspace'].concat(el('web_'+cid).checked?['web']:[]),
    max_assistance:parseInt(el('lvl_'+cid).value),
    locality:el('cloud_'+cid).checked?'cloud_allowed':'local_only',
    assessment:el('assess_'+cid).checked};
  const d=await api('/school/api/lesson.start',{class_id:cid,title:el('t_'+cid).value||'Lesson',policy:policy});
  if(d.lesson&&d.lesson.join_code){ toast('Lesson started. Join code: '+d.lesson.join_code); }
  refresh(); }
async function endLesson(id){ await api('/school/api/lesson.end',{lesson_id:id}); refresh(); }
async function sendAssignment(cid,lid){ const policy={};
  const lvl=el('asgl_'+cid).value; if(lvl!=='') policy.max_assistance=parseInt(lvl);
  if(el('asga_'+cid).checked) policy.assessment=true;
  await api('/school/api/assignment.send',{lesson_id:lid,title:el('asgt_'+cid).value,body:el('asgb_'+cid).value,policy:policy});
  show('asg_'+cid,false); toast('Assignment sent'); refresh(); }
async function ack(id){ await api('/school/api/submission.ack',{submission_id:id}); refresh(); }
async function logout(){ await api('/school/api/logout',{}); location.reload(); }
refresh();
"""
    return _page("VOOL School · Teacher", body, script)


# ---------------------------------------------------------------- student ----


def render_student_page(session: Any) -> str:
    if session is None:
        return _login_page_student()
    body = """
<div class="banner" id="banner"></div>
<div class="wrap">
  <div class="card" id="assignment"></div>
  <div class="card">
    <div id="chatlog"></div>
    <div class="row"><textarea id="say" placeholder="ask your teacher's AI for help…" style="flex:1;min-height:44px"></textarea>
      <button class="primary" onclick="send()">Ask</button></div>
    <div class="quota" id="quota"></div>
  </div>
  <div class="card">
    <h2 style="margin-top:0">My work</h2>
    <textarea id="work" placeholder="write your answer / paste your work here — this is what you hand in"></textarea>
    <div class="row"><button class="primary" onclick="prepareHandIn()">Hand in…</button>
      <button onclick="needHelp()">I need help</button></div>
  </div>
  <div id="handin" class="card hidden"></div>
</div>"""
    script = """
let STATE=null;
async function refresh(){ try{ STATE=await get('/school/api/state'); }catch(e){ if(e.status===403){location.reload();return;} throw e; } draw(); }
function draw(){
  const p=STATE.policy||{};
  el('banner').innerHTML=`<b>${esc(STATE.class||'—')}${STATE.lesson?' · '+esc(STATE.lesson.title):''}</b>`+
    `<span class="chip ${p.tool_families&&p.tool_families.includes('web')?'on':'off'}">web: ${p.tool_families&&p.tool_families.includes('web')?'allowed':'blocked'}</span>`+
    `<span class="chip">AI help: ${esc((STATE.assistance_level_name||'').toLowerCase().replace('_',' '))}</span>`+
    `<span class="chip">${p.locality==='local_only'?'school local model':'cloud allowed'}</span>`+
    (p.assessment?'<span class="chip warn">assessment</span>':'')+
    `<span style="flex:1"></span><span class="dim">${esc(STATE.me.display_name)}</span>`;
  el('assignment').innerHTML=STATE.assignment?
    `<h2 style="margin-top:0">Assignment</h2><b>${esc(STATE.assignment.title)}</b><div class="msg bot">${(STATE.assignment.body||'').replace(/</g,'&lt;')}</div>`:
    `<span class="dim">No assignment right now.</span>`;
  const q=STATE.quota||{};
  el('quota').textContent=`today: ${q.requests_used||0}/${q.requests_cap||'—'} requests · ${Math.round((q.tokens_used||0)/1000)}k/${Math.round((q.tokens_cap||0)/1000)}k tokens`;
}
async function send(){
  const text=el('say').value.trim(); if(!text) return;
  el('say').value=''; el('chatlog').insertAdjacentHTML('beforeend',`<div class="msg user">${text.replace(/</g,'&lt;')}</div>`);
  try{
    const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({messages:[{role:'user',content:text}],model:'vool',stream:false})});
    const d=await r.json().catch(()=>({}));
    const out=(d.message&&(d.message.content!=null?d.message.content:d.message))||d.response||'(no answer)';
    el('chatlog').insertAdjacentHTML('beforeend',`<div class="msg bot">${String(out).replace(/</g,'&lt;')}</div>`);
    el('chatlog').scrollTop=el('chatlog').scrollHeight;
  }catch(e){ toast(OFFLINE); }
  refresh();
}
async function needHelp(){ const note=prompt('What are you stuck on? (your teacher will see this)');
  if(note==null) return; await api('/school/api/help',{note:note}); toast('Help requested — your teacher sees it'); }
async function prepareHandIn(){
  if(!STATE.assignment){ toast('There is no assignment to hand in right now.'); return; }
  const d=await api('/school/api/handin.prepare',{assignment_id:STATE.assignment.assignment_id,work_text:el('work').value});
  const w=d.will_share||{};
  el('handin').innerHTML=`<h2 style="margin-top:0">Hand in — check what will be shared</h2>
    <table><tr><th>shared</th><td>${esc(w.student_display_name)} · ${esc(w.class)} · ${esc(w.assignment)}</td></tr>
    <tr><th>assistance summary</th><td>${w.assistance_summary.turns||0} AI turns · ${w.assistance_summary.violations||0} refused answer dumps · answer revealed: ${w.assistance_summary.answer_revealed?'yes':'no'}</td></tr></table>
    <div class="dim">${esc(d.not_shared)}</div>
    <div class="row"><button class="primary" onclick="submitHandIn()">Yes, hand in</button>
    <button onclick="show('handin',false)">Cancel</button></div>`;
  show('handin',true);
}
async function submitHandIn(){
  const d=await api('/school/api/handin.submit',{assignment_id:STATE.assignment.assignment_id,work_text:el('work').value});
  el('handin').innerHTML=`<h2 style="margin-top:0">Handed in ✓</h2>
    <div>Submission <span class="code">${esc((d.submission||{}).submission_id)}</span> · status: ${esc((d.submission||{}).status)}</div>
    ${d.idempotent?'<div class="dim">This was already handed in — nothing duplicated.</div>':''}
    <div class="dim">Receipt signed and stored; your teacher will acknowledge it.</div>`;
}
refresh();
"""
    return _page("VOOL School · Student", body, script)


def _login_page_student() -> str:
    body = """
<div class="wrap" style="max-width:440px;margin-top:12vh">
  <h1>VOOL School · Student</h1>
  <form class="card" onsubmit="login(event)">
    <div class="row"><input id="code" placeholder="Access code" aria-label="Access code" style="flex:1" autocomplete="off"></div>
    <div class="row" style="margin-top:10px"><button class="primary" type="submit">Sign in</button></div>
    <div class="say" id="loginSay" role="status" aria-live="polite"></div>
  </form>
  <form class="card" id="join" style="margin-top:14px" onsubmit="join(event)">
    <div class="dim">Have a lesson code from your teacher? Sign in above first, then type it here.</div>
    <div class="row" style="margin-top:8px"><input id="joincode" placeholder="Lesson code" aria-label="Lesson code" autocomplete="off"><button type="submit">Join lesson</button></div>
    <div class="say" id="joinSay" role="status" aria-live="polite"></div>
  </form>
</div>"""
    script = """
async function login(ev){ ev.preventDefault(); const code=el('code').value.trim();
  if(!code){ tell('Type your access code first.','loginSay'); return; }
  try { await api('/school/api/login',{access_code:code},'loginSay'); location.reload(); } catch(e) {} }
async function join(ev){ ev.preventDefault(); const code=el('joincode').value.trim();
  if(!code){ tell('Type the lesson code from your teacher first.','joinSay'); return; }
  try { await api('/school/api/lesson.join',{join_code:code},'joinSay'); location.reload(); } catch(e) {} }
"""
    return _page("VOOL School · Student", body, script)
