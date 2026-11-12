"""Settings panel: bring your chats from other AI apps (the Backup group).

The user chooses where their chats are -- an AI app found on this computer, described by what it
is, never by name, or a data export file (.zip) -- looks over the chats found (titles, dates,
message counts, a tick box each, nothing ticked until they choose), and brings in the ones they
ticked, with progress and a plain result line.

The panel only calls the chat import routes (core/web/api/chat_import_api.py) and renders what
they return. Chat titles are the user's own data and are always written as text, never as
markup. Every string resolves through the page's catalog (VOOLT). Namespace
``window.VoolChatImport``; prefix ``vci-``.
"""
from __future__ import annotations

_CSS = """
.vci-root{display:flex;flex-direction:column;gap:10px}
.vci-note{font-size:12px;color:var(--muted,#9aa1af)}
.vci-status,.vci-context-status{font-size:12.5px;min-height:16px;color:var(--muted,#9aa1af)}
.vci-status.vci-bad{color:var(--bad,#f2545b)}
.vci-status.vci-good{color:var(--ink,#e8eaf0)}
.vci-sources{border:1px solid var(--border,#262b35);border-radius:10px;padding:8px 12px;margin:0;display:flex;flex-direction:column;gap:6px;min-width:0}
.vci-sources legend{font-size:13px;font-weight:600;padding:0 4px}
.vci-source{display:flex;align-items:flex-start;gap:8px;font-size:13px;cursor:pointer}
.vci-source input{margin-top:3px}
.vci-source small{display:block;color:var(--muted,#9aa1af);font-size:11.5px}
.vci-file{display:flex;flex-direction:column;gap:4px;padding-inline-start:24px;min-width:0}
.vci-file-input{width:100%;min-width:0;max-width:100%;box-sizing:border-box}
.vci-source,.vci-source span{min-width:0;overflow-wrap:anywhere}
.vci-file[hidden]{display:none}
.vci-row{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.vci-list{border:1px solid var(--border,#262b35);border-radius:10px;max-height:340px;overflow:auto}
.vci-chat{display:flex;align-items:flex-start;gap:8px;padding:7px 10px;border-bottom:1px solid var(--border,#262b35);font-size:13px;cursor:pointer}
.vci-chat:last-child{border-bottom:none}
.vci-chat input{margin-top:3px;flex:none}
.vci-chat .vci-title{flex:1;min-width:0;overflow-wrap:anywhere}
.vci-chat .vci-meta{color:var(--muted,#9aa1af);font-size:11.5px;white-space:nowrap}
.vci-chat.vci-here{opacity:.6;cursor:default}
.vci-problems{margin:0;padding-inline-start:18px;font-size:12px;color:var(--muted,#9aa1af)}
.vci-context{padding:10px;border-bottom:1px solid var(--border,#262b35)}
.vci-context-text{white-space:pre-wrap;overflow-wrap:anywhere;max-height:160px;overflow:auto;font-size:12px}
.vci-progress{width:100%;max-width:360px}
@media (max-width:560px){.vci-chat{flex-wrap:wrap}.vci-chat .vci-meta{white-space:normal;flex-basis:100%;padding-inline-start:24px}}
"""

_JS = r"""
(function(){
if (window.VoolChatImport) return;
// Strings resolve through the page's i18n catalog (VOOLT); the fallback literals are the English
// catalog source. Chat titles are the user's data and pass through untranslated, as text.
function IT(key, fallback, params){
  try {
    if (typeof VOOLT === 'function') {
      var t = params ? VOOLT(key, params) : VOOLT(key);
      if (t && t !== key) return t;
    }
  } catch (e) {}
  if (params && typeof VOOLFMT === 'function') { try { return VOOLFMT(fallback, params); } catch (e) {} }
  return String(fallback).replace(/\{(\w+)\}/g, function(m, name){ return (params && params[name] != null) ? String(params[name]) : m; });
}
function h(tag, cls, text){ var n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
function request(method, url, body, raw){
  var options = {method: method, headers: {'Accept': 'application/json'}};
  if (raw) { options.headers['Content-Type'] = 'application/octet-stream'; options.body = raw; }
  else if (body !== undefined) { options.headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(body); }
  return fetch(url, options).then(function(r){
    return r.json().catch(function(){ return {}; }).then(function(j){ j = j || {}; if (j.ok === undefined) j.ok = r.ok; j.http_status = r.status; return j; });
  });
}
function lang(){ return (document.documentElement && document.documentElement.lang) || undefined; }
function day(iso){
  if (!iso) return '';
  var d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  try { return d.toLocaleDateString(lang(), {year: 'numeric', month: 'short', day: 'numeric'}); } catch (e) { return d.toISOString().slice(0, 10); }
}
var SOURCE_TEXT = {
  code_editor: ['chatimport.source.code_editor', 'A code editor on this computer'],
  ai_agent: ['chatimport.source.ai_agent', 'An AI agent on this computer'],
  assistant: ['chatimport.source.assistant', 'A personal AI assistant on this computer']
};
// Reader problem codes, grouped into the lines a person can act on.
var PROBLEM_TEXT = {
  file_too_large: ['chatimport.problem.too_large', 'This is too large to read safely.'],
  zip_too_large_unpacked: ['chatimport.problem.too_large', 'This is too large to read safely.'],
  conversations_too_large: ['chatimport.problem.too_large', 'This is too large to read safely.'],
  store_too_large: ['chatimport.problem.too_large', 'This is too large to read safely.'],
  zip_encrypted: ['chatimport.problem.password', 'This file is password protected. Download the export again without a password.'],
  no_conversations: ['chatimport.problem.no_history', 'No chat history VOOL can read was found here.'],
  format_unknown: ['chatimport.problem.no_history', 'No chat history VOOL can read was found here.'],
  conversations_not_list: ['chatimport.problem.no_history', 'No chat history VOOL can read was found here.'],
  store_version_unknown: ['chatimport.problem.version', 'This version of the app keeps its chats in a way VOOL cannot read yet.'],
  store_missing: ['chatimport.problem.missing', 'The chats were not found any more.'],
  store_busy: ['chatimport.problem.busy', 'The app was saving while VOOL read it. Close the app and try again.'],
  too_many_chats: ['chatimport.problem.too_many', 'Only part of the chats are listed; the rest are over the import limit.'],
  chats_internal_skipped: ['chatimport.problem.internal', 'Sessions the app ran by itself, not you, were left out.'],
  chats_without_order_skipped: ['chatimport.problem.no_order', 'Some chats were left out because the order of their messages is not saved.'],
  chat_skipped_duplicate: ['chatimport.problem.duplicate', 'Some chats appeared twice and are listed once.']
};
var PROBLEM_FALLBACK = ['chatimport.problem.unreadable', 'Some of it could not be read and was left out.'];
var FILE_PROBLEMS = {file_unreadable: 1, not_a_zip: 1, zip_too_many_members: 1, zip_unsafe_path: 1, zip_symlink: 1,
  conversations_unreadable: 1, conversations_not_utf8: 1, conversations_not_json: 1, conversations_too_deep: 1};
var ERROR_TEXT = {
  context_incomplete: ['chatimport.error.incomplete','This import did not finish. Undo it, then import again.'],
  context_withheld: ['chatimport.context.withheld','Withheld after a forget request'],
  context_copy_required: ['chatimport.context.copy','Create a separate working copy'],
  context_unavailable: ['chatimport.context.unavailable','The folder or context could not be reviewed. Check the folder and try again.'],
  bad_context: ['chatimport.context.unavailable','The folder or context could not be reviewed. Check the folder and try again.'],
  batch_in_use: ['chatimport.error.in_use', 'These chats have new work. Undo would remove it, so nothing was removed.'],
  undo_failed: ['chatimport.error.undo', 'Undo stopped. Try again to remove the remaining imported chats.'],
  busy: ['chatimport.error.busy', 'An import is already running. Wait for it to finish.'],
  preview_missing: ['chatimport.error.stale', 'This list is out of date. Show your chats again.'],
  preview_expired: ['chatimport.error.stale', 'This list is out of date. Show your chats again.'],
  upload_missing: ['chatimport.error.stale', 'This list is out of date. Show your chats again.'],
  file_too_large: ['chatimport.problem.too_large', 'This is too large to read safely.'],
  too_large: ['chatimport.problem.too_large', 'This is too large to read safely.'],
  empty_file: ['chatimport.error.empty_file', 'This file is empty.']
};
function errorText(j){
  var entry = ERROR_TEXT[j && j.error];
  return entry ? IT(entry[0], entry[1]) : IT('chatimport.error.generic', 'That did not work. Nothing was changed.');
}
function fileStatus(status){var keys={missing:['chatimport.context.missing','Missing'],outside:['chatimport.context.outside','Outside the chosen folder'],refused:['chatimport.context.refused','Refused']};var entry=keys[status]||keys.refused;return IT(entry[0],entry[1]);}
function noAnswer(){ return IT('notif.no_answer', 'VOOL did not answer; nothing was changed.'); }

function mountInto(host){
  host.textContent = '';
  var root = h('div', 'vci-root');
  host.appendChild(root);
  var fieldset = h('fieldset', 'vci-sources');
  fieldset.appendChild(h('legend', null, IT('chatimport.choose', 'Where are your chats?')));
  var sourceList = h('div', 'vci-row'); sourceList.style.flexDirection = 'column'; sourceList.style.alignItems = 'stretch';
  fieldset.appendChild(sourceList);
  root.appendChild(fieldset);
  root.appendChild(h('div', 'vci-note vci-limits', IT('chatimport.context.limits', 'Review a folder to bring project context, instructions and file references with each chat.')));
  var actions = h('div', 'vci-row');
  var lookBtn = h('button', 'btn vci-look', IT('chatimport.btn.look', 'Show my chats')); lookBtn.type = 'button'; lookBtn.disabled = true;
  actions.appendChild(lookBtn);
  root.appendChild(actions);
  root.appendChild(h('div', 'vci-note', IT('chatimport.safety', 'VOOL reads another app’s files only when you choose it here, and never changes them.')));
  var status = h('div', 'vci-status'); status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
  root.appendChild(status);
  var previewBox = h('div', 'vci-root'); previewBox.hidden = true;
  root.appendChild(previewBox);

  var fileInput = null, picked = '', preview = null, checks = [], polling = null;
  function say(text, tone){ status.textContent = text || ''; status.className = 'vci-status' + (tone === 'bad' ? ' vci-bad' : tone === 'good' ? ' vci-good' : ''); }
  function clearPreview(){ preview = null; checks = []; previewBox.hidden = true; previewBox.textContent = ''; }

  function sourceOption(value, label, detail, extra){
    var wrap = h('label', 'vci-source');
    var radio = h('input'); radio.type = 'radio'; radio.name = 'vciSource'; radio.value = value;
    radio.addEventListener('change', function(){ picked = value; clearPreview(); say(''); lookBtn.disabled = false; if (fileBox) fileBox.hidden = value !== 'file'; });
    wrap.appendChild(radio);
    var text = h('span', null, label);
    if (detail) text.appendChild(h('small', null, detail));
    wrap.appendChild(text);
    sourceList.appendChild(wrap);
    if (extra) sourceList.appendChild(extra);
    return radio;
  }
  var fileBox = null;
  function paintSources(local){
    sourceList.textContent = '';
    (local || []).forEach(function(source){
      var entry = SOURCE_TEXT[source.kind];
      if (!entry) return;
      var when = day(source.last_changed);
      sourceOption(source.id, IT(entry[0], entry[1]), when ? IT('chatimport.source.last_used', 'Last used {date}', {date: when}) : '');
    });
    fileBox = h('div', 'vci-file'); fileBox.hidden = true;
    fileInput = h('input', 'inp vci-file-input'); fileInput.type = 'file'; fileInput.accept = '.zip,application/zip';
    fileInput.setAttribute('aria-label', IT('chatimport.file.pick_aria', 'Data export file'));
    fileInput.addEventListener('change', function(){ clearPreview(); say(''); });
    fileBox.appendChild(h('span', 'vci-note', IT('chatimport.file.help', 'Most AI apps let you download your data from their settings. Pick the .zip file you received.')));
    fileBox.appendChild(fileInput);
    sourceOption('file', IT('chatimport.source.file', 'A data export file (.zip)'), '', fileBox);
    if (!(local || []).length) sourceList.insertBefore(h('div', 'vci-note', IT('chatimport.source.none_found', 'No other AI apps were found on this computer. You can bring chats from a data export file.')), sourceList.firstChild);
  }

  function problemLines(problems){
    var seen = {}, lines = [];
    (problems || []).forEach(function(code){
      var entry = PROBLEM_TEXT[code] || (FILE_PROBLEMS[code] ? ['chatimport.problem.file', 'This file could not be read as a chat export.'] : PROBLEM_FALLBACK);
      if (seen[entry[0]]) return;
      seen[entry[0]] = 1; lines.push(IT(entry[0], entry[1]));
    });
    return lines;
  }
  function selectedRefs(){ return checks.filter(function(c){ return c.box.checked && !c.box.disabled; }).map(function(c){ return c.ref; }); }
  var importBtn = null;
  function refreshImportButton(){
    if (!importBtn) return;
    var n = selectedRefs().length;
    importBtn.disabled = n === 0 || checks.some(function(c){return c.box.checked && !c.box.disabled && c.working && c.working.checked && (!c.plan || c.reviewing);});
    importBtn.textContent = n ? IT('chatimport.btn.import', '{n, plural, one {Bring in {n} chat} other {Bring in {n} chats}}', {n: n}) : IT('chatimport.btn.import_none', 'Bring in chats');
  }
  function contextControls(chat, item){
    if(chat.withheld) return h('div','vci-note',IT('chatimport.context.withheld','Withheld after a forget request'));
    var details=h('details','vci-context');
    details.appendChild(h('summary',null,IT('chatimport.context.working','Bring working context')));
    var body=h('div','vci-root');details.appendChild(body);
    var work=check(body,'working',IT('chatimport.context.working','Bring working context'));
    item.working=work;
    function field(key,label,value,multiline){
      var wrap=h('label','vci-root'),input=h(multiline?'textarea':'input','inp vci-'+key);
      wrap.appendChild(h('span','vci-note',label));input.value=value||'';
      if(!multiline) input.type='text';
      input.style.width='100%';input.style.boxSizing='border-box';
      wrap.appendChild(input);body.appendChild(wrap);return input;
    }
    var folder=field('folder',IT('setup.folder.aria','Folder path'),chat.workspace_hint);
    var old=field('old-folder',IT('chatimport.context.old_folder','Previous folder (if moved)'));
    var extra=field('instructions',IT('chatimport.context.extra','Additional instructions'),'',true);
    var memoryText=field('memory-text',IT('vx.memory.title','Memory'),'',true);
    var privateBox=check(body,'private',IT('private.banner_short','Private'));
    item.privateBox=privateBox;
    var copy=chat.already_here?check(body,'copy',IT('chatimport.context.copy','Create a separate working copy')):null;
    item.copyBox=copy;
    body.appendChild(h('div','vci-note',IT('chatimport.context.notice','Working context stays local. Files remain in your chosen folder.')));
    body.appendChild(h('div','vci-note',IT('chatimport.context.copies','Each chat receives its own inactive copies of drafts, tools and tasks.')));
    var review=h('button','btn vci-review',IT('chatimport.context.review','Review working context'));review.type='button';body.appendChild(review);
    var result=h('div','vci-root vci-context-result');body.appendChild(result);
    var note=h('div','vci-context-status');note.setAttribute('role','status');body.appendChild(note);
    function invalidate(){item.plan=null;item.options=null;result.textContent='';note.textContent=IT('chatimport.context.stale','Review again after changing these details.');refreshImportButton();}
    [folder,old,extra,memoryText,privateBox].forEach(function(input){input.addEventListener('input',invalidate);});
    work.addEventListener('change',refreshImportButton);
    if(copy) copy.addEventListener('change',function(){item.box.disabled=!copy.checked;work.checked=copy.checked;refreshImportButton();});
    review.addEventListener('click',function(){
      item.plan=null;result.textContent='';review.disabled=true;item.reviewing=true;refreshImportButton();
      request('POST','/api/chat/import/context',{preview_id:preview.preview_id,ref:chat.ref,root:folder.value,old_root:old.value,instructions:extra.value,memories:memoryText.value,private:privateBox.checked}).then(function(j){
        if(!j.ok){note.textContent=IT('chatimport.context.unavailable','The folder or context could not be reviewed. Check the folder and try again.');return;}
        var plan=j.context;item.plan=j.plan_id;item.options={};work.checked=true;
        note.textContent=IT('chatimport.context.ready','Review complete. Choose what to bring.');
        (plan.notes||[]).forEach(function(row){result.appendChild(h('pre','vci-context-text',row.text));});
        ['instructions','memories','skills','tools','schedules'].forEach(function(category){
          var labels={instructions:'chatimport.context.rules',memories:'chatimport.context.memories',skills:'chatimport.context.skills',tools:'chatimport.context.tools',schedules:'chatimport.context.schedules'};
          var fallbacks={instructions:'Save instructions as RULES',memories:'Save reviewed memories',skills:'Save skills as local drafts',tools:'Save tools disabled',schedules:'Save tasks paused'};
          var rows=plan[category]||[];
          var option=check(result,category,IT(labels[category],fallbacks[category])+' ('+rows.length+')');
          option.disabled=!rows.length||(plan.private&&(category==='instructions'||category==='memories'));
          option.addEventListener('change',function(){item.options[category]=option.checked;});
          rows.forEach(function(row){var text=row.text||row.body||JSON.stringify(row);result.appendChild(h('pre','vci-context-text',text));});
        });
        if((plan.files||[]).length) result.appendChild(h('strong',null,IT('panel.tab_files','Files')));
        (plan.files||[]).forEach(function(file){
          var entry={exists:['vx.toolbelt.st_available','Available'],missing:['chatimport.context.missing','Missing'],outside:['chatimport.context.outside','Outside the chosen folder'],refused:['chatimport.context.refused','Refused']}[file.status]||['chatimport.context.refused','Refused'];
          result.appendChild(h('div','vci-note vci-file-reference',IT(entry[0],entry[1])+' · '+file.source+' → '+file.path));
        });
        [['models','chatimport.context.models','Recorded models'],['providers','chatimport.context.providers','Providers (add keys in Settings)']].forEach(function(pair){
          result.appendChild(h('div','vci-note',IT(pair[1],pair[2])+': '+(plan[pair[0]]||[]).join(', ')));
        });
        if((plan.notices||[]).indexOf('conditional_rules_staged')>=0)result.appendChild(h('div','vci-note',IT('chatimport.context.conditional','Conditional instructions are saved as drafts. Review their conditions before using them.')));
        if((plan.warnings||[]).length)result.appendChild(h('div','vci-note',IT('chatimport.context.warnings','Some source context was left out because it is unsupported, unreadable, or over the review limit. Check the source before continuing.')));
        var modelWrap=h('label','vci-root');modelWrap.appendChild(h('span','vci-note',IT('settings.row.local_models.label','Local models')));
        var models=h('select','inp vci-model');var keep=h('option',null,'—');keep.value='';models.appendChild(keep);
        (j.local_models||[]).forEach(function(name){var option=h('option',null,name);option.value=name;models.appendChild(option);});
        modelWrap.appendChild(models);result.appendChild(modelWrap);
        models.addEventListener('change',function(){item.options.model=models.value;});
      }).catch(function(){note.textContent=noAnswer();}).finally(function(){review.disabled=false;item.reviewing=false;refreshImportButton();});
    });
    return details;
  }
  function check(host,cls,text){var label=h('label','vci-source'),box=h('input','vci-'+cls);box.type='checkbox';label.appendChild(box);label.appendChild(h('span',null,text));host.appendChild(label);return box;}

  function paintPreview(j){
    preview = j; checks = [];
    previewBox.textContent = ''; previewBox.hidden = false;
    var chats = j.chats || [];
    var lines = problemLines(j.problems);
    if (!chats.length) {
      say(lines.length ? lines[0] : IT('chatimport.preview.none', 'No chats were found here.'), lines.length ? 'bad' : '');
      if (lines.length > 1) { var more = h('ul', 'vci-problems'); lines.slice(1).forEach(function(line){ more.appendChild(h('li', null, line)); }); previewBox.appendChild(more); }
      else previewBox.hidden = true;
      return;
    }
    say(IT('chatimport.preview.summary', '{n, plural, one {Found {n} chat.} other {Found {n} chats.}}', {n: chats.length}) + (j.already_here ? ' ' + IT('chatimport.preview.already', '{n, plural, one {{n} is already here.} other {{n} are already here.}}', {n: j.already_here}) : ''));
    if (lines.length) { var list = h('ul', 'vci-problems'); lines.forEach(function(line){ list.appendChild(h('li', null, line)); }); previewBox.appendChild(list); }
    if (j.truncated) previewBox.appendChild(h('div', 'vci-note', IT('chatimport.truncated', 'Some very long chats or messages were shortened to fit.')));
    previewBox.appendChild(h('div', 'vci-note', IT('chatimport.preview.choose_note', 'Nothing is ticked yet. Tick the chats you want to bring in, or select all.')));
    var tools = h('div', 'vci-row');
    var all = h('button', 'btn vci-all', IT('chatimport.btn.select_all', 'Select all')); all.type = 'button';
    var none = h('button', 'btn vci-none', IT('chatimport.btn.select_none', 'Select none')); none.type = 'button';
    all.addEventListener('click', function(){ checks.forEach(function(c){ if (!c.box.disabled) c.box.checked = true; }); refreshImportButton(); });
    none.addEventListener('click', function(){ checks.forEach(function(c){ c.box.checked = false; }); refreshImportButton(); });
    tools.appendChild(all); tools.appendChild(none);
    previewBox.appendChild(tools);
    var listBox = h('div', 'vci-list'); listBox.setAttribute('role', 'group'); listBox.setAttribute('aria-label', IT('chatimport.list_aria', 'Chats found'));
    chats.forEach(function(chat){
      var row = h('label', 'vci-chat' + (chat.already_here ? ' vci-here' : ''));
      var box = h('input'); box.type = 'checkbox'; box.checked = false; box.disabled = !!chat.already_here || !!chat.withheld;
      box.addEventListener('change', refreshImportButton);
      row.appendChild(box);
      row.appendChild(h('span', 'vci-title', chat.withheld ? IT('chatimport.context.withheld','Withheld after a forget request') : (chat.title || IT('chatimport.chat.untitled', 'Untitled chat'))));
      var meta = [day(chat.updated_at || chat.created_at), IT('chatimport.chat.messages', '{n, plural, one {{n} message} other {{n} messages}}', {n: chat.message_count || 0})];
      if (chat.already_here) meta.push(IT('chatimport.chat.already', 'Already here'));
      row.appendChild(h('span', 'vci-meta', meta.filter(Boolean).join(' · ')));
      listBox.appendChild(row);
      var item={ref:chat.ref,box:box};checks.push(item);
      listBox.appendChild(contextControls(chat,item));
    });
    previewBox.appendChild(listBox);
    var consent = h('label', 'vci-source');
    var continueBox = h('input', 'vci-continue'); continueBox.type = 'checkbox'; continueBox.checked = false;
    consent.appendChild(continueBox);
    consent.appendChild(h('span', null, IT('chatimport.continue', 'Use this history with a local model when I continue these chats.')));
    previewBox.appendChild(consent);
    var go = h('div', 'vci-row');
    importBtn = h('button', 'btn primary vci-import', ''); importBtn.type = 'button';
    var cancel = h('button', 'btn vci-cancel', IT('chatimport.btn.cancel', 'Cancel')); cancel.type = 'button';
    importBtn.addEventListener('click', startImport);
    cancel.addEventListener('click', function(){
      request('POST', '/api/chat/import/discard', {}).catch(function(){});
      clearPreview(); say('');
    });
    go.appendChild(importBtn); go.appendChild(cancel);
    previewBox.appendChild(go);
    refreshImportButton();
  }
  function look(){
    if (!picked) return;
    clearPreview();
    lookBtn.disabled = true;
    var done = function(){ lookBtn.disabled = false; };
    if (picked === 'file') {
      var file = fileInput && fileInput.files && fileInput.files[0];
      if (!file) { say(IT('chatimport.error.pick_file', 'Choose a .zip file first.'), 'bad'); done(); return; }
      say(IT('chatimport.status.opening', 'Opening the file…'));
      request('POST', '/api/chat/import/upload', undefined, file).then(function(up){
        if (!up.ok) { say(errorText(up), 'bad'); done(); return; }
        say(IT('chatimport.status.reading', 'Reading your chats…'));
        return request('POST', '/api/chat/import/preview', {upload_id: up.upload_id}).then(function(j){
          done();
          if (!j.ok) { say(errorText(j), 'bad'); return; }
          paintPreview(j);
        });
      }).catch(function(){ done(); say(noAnswer(), 'bad'); });
      return;
    }
    say(IT('chatimport.status.reading', 'Reading your chats…'));
    request('POST', '/api/chat/import/preview', {source: picked}).then(function(j){
      done();
      if (!j.ok) { say(errorText(j), 'bad'); return; }
      paintPreview(j);
    }).catch(function(){ done(); say(noAnswer(), 'bad'); });
  }
  lookBtn.addEventListener('click', look);

  var progress = h('progress', 'vci-progress'); progress.hidden = true; progress.max = 1; progress.value = 0;
  progress.setAttribute('aria-hidden', 'true'); // the status line above reads the same progress aloud
  root.insertBefore(progress, previewBox);
  function resultText(outcome){
    var parts = [IT('chatimport.result.brought', '{n, plural, one {Brought in {n} chat.} other {Brought in {n} chats.}}', {n: outcome.brought_in || 0})];
    if (outcome.already_here) parts.push(IT('chatimport.result.already', '{n, plural, one {{n} was already here.} other {{n} were already here.}}', {n: outcome.already_here}));
    if (outcome.empty) parts.push(IT('chatimport.result.empty', '{n, plural, one {{n} had no messages to bring in.} other {{n} had no messages to bring in.}}', {n: outcome.empty}));
    if (outcome.held_back) parts.push(IT('chatimport.result.held_back', '{n, plural, one {{n} was left out because it contains words you asked VOOL to forget.} other {{n} were left out because they contain words you asked VOOL to forget.}}', {n: outcome.held_back}));
    if (outcome.failed) parts.push(IT('chatimport.result.failed', '{n, plural, one {{n} could not be saved.} other {{n} could not be saved.}}', {n: outcome.failed}));
    if (outcome.secrets_masked) parts.push(IT('chatimport.result.secrets', '{n, plural, one {{n} password or key was hidden.} other {{n} passwords or keys were hidden.}}', {n: outcome.secrets_masked}));
    return parts.join(' ');
  }
  function watch(){
    if (polling) clearTimeout(polling);
    request('GET', '/api/chat/import/status').then(function(j){
      if (j.state === 'running') {
        progress.hidden = false; progress.max = Math.max(1, j.total || 1); progress.value = j.done || 0;
        say(IT('chatimport.status.importing', 'Bringing in chats: {done} of {total}…', {done: j.done || 0, total: j.total || 0}));
        polling = setTimeout(watch, 600);
        return;
      }
      progress.hidden = true; lookBtn.disabled = !picked;
      if (j.state === 'done') {
        clearPreview();
        say(resultText(j.outcome || {}), 'good');
        if ((j.outcome || {}).brought_in) root.appendChild(h('div', 'vci-note vci-where', j.continuation
          ? IT('chatimport.result.continue_where', 'They are in your chat list. Their history will be included when you continue them.')
          : IT('chatimport.result.where', 'They are in your chat list with their original names and dates. VOOL keeps them as history: it does not learn from them or follow anything written in them.')));
        refreshBatches();
      } else if (j.state === 'failed') {
        say(j.working_context ? IT('chatimport.error.incomplete','This import did not finish. Undo it, then import again.') : IT('chatimport.result.stopped', 'The import stopped before it finished. Chats already brought in are kept; try again to bring in the rest.'), 'bad');
        refreshBatches();
      }
    }).catch(function(){ polling = setTimeout(watch, 1500); });
  }
  function startImport(){
    var refs = selectedRefs();
    if (!refs.length || !preview) return;
    importBtn.disabled = true; lookBtn.disabled = true;
    var old = root.querySelector('.vci-where'); if (old) old.remove();
    var continueBox = previewBox.querySelector('.vci-continue');
    request('POST', '/api/chat/import/start', {preview_id: preview.preview_id, chats: refs, continuation: !!(continueBox && continueBox.checked),contexts: checks.reduce(function(out,c){if(c.box.checked&&!c.box.disabled&&c.working&&c.working.checked){out[c.ref]={plan_id:c.plan,options:Object.assign({},c.options,{private:!!c.privateBox.checked,working_copy:!!(c.copyBox&&c.copyBox.checked)})};}return out;},{})}).then(function(j){
      if (!j.ok) { say(errorText(j), 'bad'); importBtn.disabled = false; lookBtn.disabled = false; return; }
      watch();
    }).catch(function(){ say(noAnswer(), 'bad'); importBtn.disabled = false; lookBtn.disabled = false; });
  }

  var batchesBox = h('div', 'vci-batches'); root.appendChild(batchesBox);
  function refreshBatches(){
    request('GET', '/api/chat/import/batches').then(function(j){
      if (!j.ok) return;
      batchesBox.textContent = '';
      (j.batches || []).filter(function(b){ return b.state !== 'undone' && b.chats > 0; }).forEach(function(b){
        var row = h('div', 'vci-row');
        var when = day(b.created_at);
        try { when = new Date(b.created_at).toLocaleString(lang()); } catch (e) {}
        row.appendChild(h('span', 'vci-note', when + ' · ' + IT('chatimport.result.brought', '{n, plural, one {Brought in {n} chat.} other {Brought in {n} chats.}}', {n: b.chats})));
        if(b.state==='failed')row.appendChild(h('span','vci-note',IT('chatimport.error.incomplete','This import did not finish. Undo it, then import again.')));
        var undo = h('button', 'btn vci-undo', IT('chatimport.undo', 'Undo import')); undo.type = 'button';
        undo.addEventListener('click', function(){
          undo.disabled = true;
          request('POST', '/api/chat/import/undo', {batch_id: b.batch_id}).then(function(result){
            if (!result.ok) { say(errorText(result), 'bad'); undo.disabled = false; return; }
            say(IT('chatimport.undone', 'Import removed.'), 'good');
            refreshBatches();
          }).catch(function(){ say(noAnswer(), 'bad'); undo.disabled = false; });
        });
        row.appendChild(undo); batchesBox.appendChild(row);
        (b.contexts||[]).forEach(function(context){
          var overview=h('details','vci-context vci-saved-context');
          overview.appendChild(h('summary',null,IT('chatimport.context.working','Bring working context')));
          if(context.project)overview.appendChild(h('div','vci-note',context.project.name+' · '+context.project.root));
          (context.files||[]).forEach(function(file){
            overview.appendChild(h('div','vci-note',file.source+' → '+file.path+' · '+(file.status==='exists'?IT('vx.toolbelt.st_available','Available'):fileStatus(file.status))));
          });
          [['models','chatimport.context.models','Recorded models'],['providers','chatimport.context.providers','Providers (add keys in Settings)']].forEach(function(pair){
            overview.appendChild(h('div','vci-note',IT(pair[1],pair[2])+': '+(context[pair[0]]||[]).join(', ')));
          });
          [['rules','chatimport.context.rules','Save instructions as RULES'],['skills','chatimport.context.skills','Save skills as local drafts'],['tools','chatimport.context.tools','Save tools disabled'],['schedules','chatimport.context.schedules','Save tasks paused']].forEach(function(pair){
            var rows=(context.resources||{})[pair[0]]||[];
            if(!rows.length)return;
            var saved=h('details','vci-saved-resources');
            saved.appendChild(h('summary',null,IT(pair[1],pair[2])+' ('+rows.length+')'));
            rows.forEach(function(row){saved.appendChild(h('pre','vci-context-text',row.id+'\n'+row.text));});
            overview.appendChild(saved);
          });
          batchesBox.appendChild(overview);
        });
      });
    }).catch(function(){});
  }
  refreshBatches();

  request('GET', '/api/chat/import/sources').then(function(j){
    if (!j.ok) { paintSources([]); say(errorText(j), 'bad'); return; }
    paintSources(j.local || []);
    request('GET', '/api/chat/import/status').then(function(s){ if (s.state === 'running') watch(); }).catch(function(){});
  }).catch(function(){ paintSources([]); say(noAnswer(), 'bad'); });
  return true;
}
window.VoolChatImport = Object.freeze({mountInto: mountInto});
})();
"""


def render_chat_import_settings_fragment() -> str:
    """The chat import panel, mounted by the Settings page's Backup group."""
    return "<style>" + _CSS + "</style><script>" + _JS + "</script>"


__all__ = ["render_chat_import_settings_fragment"]
