from __future__ import annotations


def render_disclosure_fragment() -> str:
    return r'''
<style>
#cloudDisclosure,#cloudSentView,#cloudCompare{box-sizing:border-box;background:var(--panel,#171b22);color:var(--text,#eef2f8);border:1px solid #7c8da066;box-shadow:0 18px 80px #0008;font:inherit}
#cloudDisclosure::backdrop,#cloudSentView::backdrop,#cloudCompare::backdrop{background:#050b15b8;backdrop-filter:blur(3px)}
#cloudDisclosure h2,#cloudSentView h2,#cloudCompare h2{margin-top:0;line-height:1.25}
#cloudDisclosure button,#cloudSentView button,#cloudCompare button,#cloudDisclosure select{font:inherit;color:inherit;background:#7890ae18;border:1px solid #8ba0bb88;border-radius:8px;padding:10px 14px;cursor:pointer;max-width:100%}
#cloudDisclosure button:focus-visible,#cloudSentView button:focus-visible,#cloudCompare button:focus-visible{outline:3px solid #78b5ff;outline-offset:3px}
#cloudDisclosure button:disabled,#cloudCompare button:disabled{opacity:.55;cursor:default}
#cloudDisclosure [data-disclosure-action=send],#cloudCompareRun{background:#2462b5;color:white;border-color:#528dda}
#cloudDisclosureParts{border:1px solid #8ba0bb66;border-radius:8px;max-height:24vh;overflow:auto;margin-block:16px}
#cloudDisclosureParts label{padding:8px 2px;overflow-wrap:anywhere}
#cloudDisclosure pre,#cloudSentView pre{padding:14px;background:#0003;border-radius:8px}
#cloudCompareModels label{padding:8px 0}#cloudCompareModels small{display:block;margin-inline-start:22px}
@media(max-width:540px){#cloudDisclosure,#cloudSentView,#cloudCompare{padding:16px!important;max-width:96vw!important}#cloudDisclosure button{flex:1 1 45%}}
</style>
<dialog id="cloudDisclosure" aria-labelledby="cloudDisclosureTitle" style="max-width:min(820px,90vw);max-height:90vh;border:1px solid #888;border-radius:14px;padding:24px">
  <h2 id="cloudDisclosureTitle" data-disclosure-text="title">See what leaves your computer</h2>
  <p id="cloudDisclosureDestination"></p>
  <p data-disclosure-text="review_before_send">Review the complete request below. Nothing is sent until you choose.</p>
  <p id="cloudDisclosureFilesNote" data-disclosure-text="task_files_note" hidden>These files belong to your task. Only content in the complete request below is sent.</p>
  <div id="cloudDisclosureFiles"></div>
  <fieldset id="cloudDisclosureParts"><legend data-disclosure-text="sections">Sections to send</legend></fieldset>
  <details><summary>Complete request</summary><pre id="cloudDisclosureBody" style="white-space:pre-wrap;overflow-wrap:anywhere;max-height:35vh;overflow:auto"></pre></details>
  <p id="cloudDisclosureNote" role="status"></p>
  <div style="display:flex;flex-wrap:wrap;gap:10px;margin-top:18px">
    <select id="cloudDisclosureLocal" hidden aria-label="Local model"></select>
    <button type="button" data-disclosure-action="local" data-disclosure-text="use_local" hidden>Use a local model instead</button>
    <button type="button" data-disclosure-action="parts" data-disclosure-text="parts">Send only these parts</button>
    <button type="button" data-disclosure-action="replace" data-disclosure-text="replace">Replace names and details</button>
    <button type="button" data-disclosure-action="send" data-disclosure-text="send">Send as shown</button>
    <button type="button" data-disclosure-action="cancel" data-disclosure-text="dont_send">Don't send</button>
  </div>
</dialog>
<dialog id="cloudSentView" aria-labelledby="cloudSentTitle" style="width:min(860px,92vw);max-height:90vh;border-radius:14px;padding:24px">
  <h2 id="cloudSentTitle" data-disclosure-text="sent_link">See what was sent</h2><p data-disclosure-text="ephemeral">Available in this open chat for up to five minutes. Stored copies are off.</p>
  <div id="cloudSentList"></div><button type="button" id="cloudSentClose" data-disclosure-text="close">Close</button>
</dialog>
<dialog id="cloudCompare" aria-labelledby="cloudCompareTitle" style="width:min(1100px,94vw);max-height:90vh;border-radius:14px;padding:24px">
  <h2 id="cloudCompareTitle" data-disclosure-text="compare_title">Compare on this task</h2>
  <p data-disclosure-text="compare_note">Choose up to two models you have set up. Each cloud request gets its own preview. Existing spending limits apply.</p>
  <div id="cloudCompareModels"></div><p id="cloudCompareStatus" role="status"></p>
  <button type="button" id="cloudCompareRun" data-disclosure-text="compare">Compare</button><button type="button" id="cloudCompareStop" data-disclosure-text="stop_compare" hidden>Stop comparison</button>
  <div id="cloudCompareResults" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(min(280px,100%),1fr));gap:18px;margin-block:20px"></div>
  <p data-disclosure-text="preference_note">Your choice is saved only on this computer. It does not change which model is used.</p>
  <button type="button" id="cloudCompareClose" data-disclosure-text="close">Close</button>
</dialog>
<script>
(() => {
  const dialog=document.getElementById('cloudDisclosure'); if(!dialog)return;
  const tr=(key,fallback)=>{try{const value=window.VOOLT?.('disclosure.'+key);return value&&value!=='disclosure.'+key?value:fallback;}catch(_){return fallback;}};
  document.querySelectorAll('[data-disclosure-text]').forEach(node=>node.textContent=tr(node.dataset.disclosureText,node.textContent));
  document.getElementById('cloudDisclosureLocal').setAttribute('aria-label',tr('local_model','Local model'));
  const el=(tag,text)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;return node;};
  let current=null, busy=false, receipts=[], compareSource=null, compareJob=null, renderedComparison="";
  const jobsBySource=new Map();
  const sentView=document.getElementById('cloudSentView'),compareView=document.getElementById('cloudCompare');
  document.getElementById('cloudSentClose').onclick=()=>sentView.close();
  document.getElementById('cloudCompareClose').onclick=()=>compareView.close();
  async function api(path,data){const r=await fetch('/api/disclosure/'+path,{cache:'no-store',...(data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})});const value=await r.json();if(!r.ok)throw new Error(tr('expired','This request changed or expired. Prepare the task again.'));return value;}
  function showSent(chat,request){
    const list=document.getElementById('cloudSentList');list.replaceChildren();
    const matches=receipts.filter(p=>p.chat_id===chat&&(normalize(p.turn_id)===normalize(request)||p.council_run_id===request)&&p.state==='sent');
    if(!matches.length)list.append(el('p',tr('expired','This request changed or expired. Prepare the task again.')));
    for(const p of matches){
      const section=el('section');section.append(el('h3',new URL(p.destination).host));
      const details=el('details'),summary=el('summary',tr('complete','Complete request')),raw=el('pre',p.body);raw.style.cssText='white-space:pre-wrap;overflow-wrap:anywhere;max-height:45vh;overflow:auto';details.append(summary,raw);section.append(details);
      if(p.omitted_sections)section.append(el('p',tr('omitted','Privacy rules kept some content on your computer. This request excludes it.')));
      if(p.can_compare){const button=el('button',tr('try_other','Try this with another model'));button.type='button';button.onclick=()=>openCompare(p);section.append(button);}
      list.append(section);
    }
    if(!sentView.open)sentView.showModal();
  }
  const normalize=value=>String(value||'').replace(/^req:http:/,'');
  function mount(host,chat,request){
    if(host.querySelector('.cloud-disclosure-answer'))return;
    const button=el('button',tr('sent_link','See what was sent'));button.className='msg-act cloud-disclosure-answer';button.type='button';button.hidden=true;
    button.onclick=()=>showSent(chat,request);(host.querySelector('.msg-actions')||host).append(button);
  }
  window.VoolDisclosure=Object.freeze({mount});
  async function openCompare(source){
    compareSource=source;compareJob=jobsBySource.get(source.id)||null;renderedComparison='';
    const status=document.getElementById('cloudCompareStatus');status.textContent='';
    const choices=document.getElementById('cloudCompareModels');choices.replaceChildren();document.getElementById('cloudCompareResults').replaceChildren();
    document.getElementById('cloudCompareRun').disabled=!!compareJob;document.getElementById('cloudCompareStop').hidden=!compareJob;
    if(sentView.open)sentView.close();if(!compareView.open)compareView.showModal();
    try{
      if(compareJob){await pollComparison();return;}
      const data=await api('compare/models?id='+encodeURIComponent(source.id));
      if(!data.models.length)choices.append(el('p',tr('no_models','No other compatible model is ready. Set up a model in Settings.')));
      for(const model of data.models){const label=el('label');label.style.display='block';const check=el('input');check.type='checkbox';check.value=model.id;check.disabled=!model.available;
        label.append(check,document.createTextNode(' '+model.model+' · '+model.provider+(model.paid?' · '+tr('paid','May charge your account'):'')));
        if(!model.available)label.append(el('small',' · '+tr('unavailable','Not available for this prepared task')));choices.append(label);
        check.onchange=()=>{const selected=choices.querySelectorAll('input:checked').length;for(const input of choices.querySelectorAll('input'))if(!input.checked&&data.models.find(m=>m.id===input.value)?.available)input.disabled=selected>=2;};
      }
    }catch(error){status.textContent=error.message;}
  }
  document.getElementById('cloudCompareRun').onclick=async()=>{
    const button=document.getElementById('cloudCompareRun'),status=document.getElementById('cloudCompareStatus');
    const models=Array.from(document.querySelectorAll('#cloudCompareModels input:checked')).map(n=>n.value);
    if(!models.length){status.textContent=tr('choose_models','Choose one or two models.');return;}
    button.disabled=true;
    try{const job=await api('compare/start',{id:compareSource.id,sha256:compareSource.sha256,models});compareJob=job.id;jobsBySource.set(compareSource.id,job.id);document.getElementById('cloudCompareStop').hidden=false;status.textContent=tr('running','Comparing. Review each cloud request when it appears.');}
    catch(error){status.textContent=error.message;button.disabled=false;}
  };
  document.getElementById('cloudCompareStop').onclick=async()=>{if(!compareJob)return;await api('compare/cancel',{id:compareJob});document.getElementById('cloudCompareStatus').textContent=tr('stopping','Stopping. Any call already sent may still be billed.');};
  const number=value=>value===null||value===undefined?tr('unknown','Unknown'):new Intl.NumberFormat(undefined,{maximumFractionDigits:2}).format(value);
  function formatBill(metrics,unknown){
    const declared=Object.prototype.hasOwnProperty.call(metrics,'billed_amount');
    const amount=declared?metrics.billed_amount:metrics.billed_usd;
    const currency=declared?metrics.billed_currency:'USD';
    if(typeof amount!=='number'||!Number.isFinite(amount)||amount<0||!['USD','USDC'].includes(currency))return unknown;
    return new Intl.NumberFormat(undefined,{maximumSignificantDigits:8}).format(amount)+' '+currency;
  }
  function answerColumn(model,answer,metrics,state,measurementError){
    metrics=metrics||{first_token_ms:null,total_ms:null,prompt_tokens:null,output_tokens:null,billed_usd:null};
    const column=el('section');column.style.cssText='min-width:0;border:1px solid #9998;border-radius:10px;padding:16px';column.append(el('h3',model));
    const text=el('div',answer||tr('no_answer','No answer returned'));text.style.cssText='white-space:pre-wrap;overflow-wrap:anywhere';column.append(text);
    if(metrics){const line=el('p',tr('first_token','First text')+': '+number(metrics.first_token_ms)+' ms · '+tr('total_time','Total')+': '+number(metrics.total_ms)+' ms');column.append(line);
      column.append(el('p',tr('tokens','Tokens in / out')+': '+number(metrics.prompt_tokens)+' / '+number(metrics.output_tokens)));
      column.append(el('p',tr('billed','Billed cost')+': '+formatBill(metrics,tr('unknown','Unknown'))));}
    if(measurementError||metrics.accounting_error)column.append(el('p',tr('measurements_unavailable','Some measurements could not be saved or read.')));
    if(state==='completed'){const choose=el('button',tr('prefer','I prefer this answer'));choose.type='button';choose.onclick=async()=>{await api('compare/prefer',{id:compareJob,model});document.getElementById('cloudCompareStatus').textContent=tr('preference_saved','Choice saved on this computer. Model selection is unchanged.');};column.append(choose);}
    return column;
  }
  async function pollComparison(){if(!compareJob||!compareView.open)return;try{const data=await api('compare/job?id='+encodeURIComponent(compareJob));
    const stamp=JSON.stringify(data);if(stamp===renderedComparison)return;renderedComparison=stamp;
    const host=document.getElementById('cloudCompareResults');host.replaceChildren();host.append(answerColumn(data.original_model,data.original,data.original_measured,'completed'));
    for(const result of data.results){const column=answerColumn(result.model,result.answer,result.measured,result.state,result.accounting_error);if(result.error)column.append(el('p',result.sent===false?tr(result.refusal_reason==='spending'?'spend_refused':'unsent',result.refusal_reason==='spending'?'The spending limit stopped this request. Nothing was sent.':'This request was stopped before sending.'):tr('model_failed','This model did not finish. It may still have been billed.')));
      if(result.same_input===false)column.append(el('p',tr('changed_input','The inputs differ because of your choices or privacy rules.')));
      if(result.spend_state==='billing_ambiguous')column.append(el('p',tr('held','The final charge is unknown. Its spending hold remains.')));host.append(column);}
    if(data.state!=='running'){document.getElementById('cloudCompareStop').hidden=true;document.getElementById('cloudCompareStatus').textContent=tr(data.state==='cancelled'?'cancelled':'done',data.state==='cancelled'?'Comparison stopped.':'Comparison finished.');}
  }catch(error){document.getElementById('cloudCompareStatus').textContent=error.message;}}

  const note=document.getElementById('cloudDisclosureNote');
  const title=document.getElementById('cloudDisclosureDestination');
  const body=document.getElementById('cloudDisclosureBody');
  const parts=document.getElementById('cloudDisclosureParts');
  async function choose(action){
    if(!current||busy)return; busy=true;
    try{
      const sections=Array.from(parts.querySelectorAll('input:checked')).map(x=>x.value);
      const local=action==='local';
      const response=await fetch(local?'/api/disclosure/local':'/api/disclosure/decision',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:current.id,sha256:current.sha256,action,sections,model:document.getElementById('cloudDisclosureLocal').value})});
      if(!response.ok)throw new Error(tr('expired','This request changed or expired. Prepare the task again.'));
      const retry=local?await response.json():null;
      dialog.close(); current=null;
      if(retry)await window.VoolDisclosureLocalRetry(retry);
    }catch(error){note.textContent=error.message;}finally{busy=false;}
  }
  dialog.querySelectorAll('[data-disclosure-action]').forEach(button=>button.addEventListener('click',()=>choose(button.dataset.disclosureAction)));
  dialog.addEventListener('cancel',event=>{event.preventDefault();choose('cancel');});
  async function poll(){
    try{
      const response=await fetch('/api/disclosure/pending',{cache:'no-store'});if(!response.ok)return;
      const data=await response.json();receipts=data.requests;
      document.querySelectorAll('[data-request-id][data-chat-id]').forEach(host=>{mount(host,host.dataset.chatId,host.dataset.requestId);const button=host.querySelector('.cloud-disclosure-answer');if(button)button.hidden=!receipts.some(p=>p.chat_id===host.dataset.chatId&&(normalize(p.turn_id)===normalize(host.dataset.requestId)||(host.dataset.councilRun&&p.council_run_id===host.dataset.councilRun))&&p.state==='sent');const omitted=receipts.some(p=>p.chat_id===host.dataset.chatId&&(normalize(p.turn_id)===normalize(host.dataset.requestId)||(host.dataset.councilRun&&p.council_run_id===host.dataset.councilRun))&&p.omitted_sections>0);let notice=host.querySelector('.cloud-disclosure-omitted');if(omitted&&!notice){notice=el('p',tr('omitted','Privacy rules kept some content on your computer. This request excludes it.'));notice.className='cloud-disclosure-omitted';host.append(notice);}if(notice)notice.hidden=!omitted;});
      await pollComparison();
      const activeChat=window.VoolDisclosureActiveChat?.();
      const next=data.requests.find(item=>item.state==='waiting'&&(!activeChat||item.chat_id===activeChat));
      if(!next){if(current&&!busy){dialog.close();current=null;}return;}
      if(current&&current.id===next.id)return;
      current=next;
      for(const action of ['parts','replace'])dialog.querySelector('[data-disclosure-action="'+action+'"]').hidden=next.can_transform===false;
      parts.hidden=next.can_transform===false;
      title.textContent=tr('destination','Destination')+': '+next.provider+' · '+new URL(next.destination).host;
      body.textContent=next.body;note.textContent=(next.omitted_sections?tr('omitted','Privacy rules kept some content on your computer. This request excludes it.')+' ':'')+tr('best_effort','Best effort: check the preview. Unrecognized details may still be present.');
      document.getElementById('cloudDisclosureFilesNote').hidden=!next.attachments.length;
      const files=document.getElementById('cloudDisclosureFiles');files.replaceChildren();
      for(const file of next.attachments){const row=document.createElement('p');row.textContent=file.name+' · '+(file.size===null||file.size===undefined?tr('unknown','Unknown'):new Intl.NumberFormat().format(file.size)+' '+tr('bytes','bytes'));files.append(row);}
      parts.replaceChildren(); const legend=document.createElement('legend');legend.textContent=tr('sections','Sections to send');parts.append(legend);
      const payload=JSON.parse(next.body);
      (payload.messages||[]).forEach((message,index)=>{
        const blocks=Array.isArray(message.content)?message.content:[message.content];
        blocks.forEach((block,n)=>{
          const label=document.createElement('label');label.style.display='block';
          const input=document.createElement('input');input.type='checkbox';input.value=Array.isArray(message.content)?'b:'+index+':'+n:Array.isArray(message.images)&&message.images.length?'t:'+index:'m:'+index;input.checked=true;
          const text=typeof block==='string'?block:block?.type==='text'?String(block.text||''):block?.type==='image_url'?tr('request_image','Image in this request'):JSON.stringify(block);
          label.append(input,document.createTextNode(' '+(index+1)+': '+text));parts.append(label);
          if(block&&block.type==='image_url'&&/^data:image\/(png|jpeg|webp|gif);base64,/.test(String(block.image_url?.url||''))){
            const img=document.createElement('img');img.src=block.image_url.url;img.alt=tr('request_image','Image in this request');img.style.maxWidth='240px';parts.append(img);
          }
        });
        (Array.isArray(message.images)?message.images:[]).forEach((encoded,n)=>{
          const label=el('label');label.style.display='block';const input=el('input');input.type='checkbox';input.value='i:'+index+':'+n;input.checked=true;
          label.append(input,document.createTextNode(' '+tr('request_image','Image in this request')+' '+(n+1)));parts.append(label);
          try{
            const head=atob(encoded.slice(0,48));
            const mime=head.startsWith('\x89PNG\r\n\x1a\n')?'image/png':head.startsWith('\xff\xd8\xff')?'image/jpeg':/^GIF8[79]a/.test(head)?'image/gif':head.startsWith('RIFF')&&head.slice(8,12)==='WEBP'?'image/webp':'';
            if(mime){const img=el('img');img.src='data:'+mime+';base64,'+encoded;img.alt=tr('request_image','Image in this request');img.style.maxWidth='240px';parts.append(img);}
            else label.append(document.createTextNode(' · '+tr('unknown','Unknown')));
          }catch(_){label.append(document.createTextNode(' · '+tr('unknown','Unknown')));}
        });
      });
      const select=document.getElementById('cloudDisclosureLocal');
      const localButton=dialog.querySelector('[data-disclosure-action="local"]');
      select.replaceChildren();select.hidden=true;localButton.hidden=true;
      const previewId=next.id;
      fetch('/api/disclosure/local-models?id='+encodeURIComponent(previewId),{cache:'no-store'}).then(r=>r.json()).then(data=>{
        if(!current||current.id!==previewId)return;
        for(const model of data.models||[]){const option=document.createElement('option');option.value=model.id;option.textContent=model.id;select.append(option);}
        select.hidden=!select.options.length;localButton.hidden=!select.options.length;
      }).catch(()=>{});
      if(!dialog.open)dialog.showModal();
    }catch(_error){}
  }
  setInterval(poll,1000);poll();
})();
</script>'''
