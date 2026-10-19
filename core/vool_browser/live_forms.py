"""Bounded form observations and owner-only submission review."""
from __future__ import annotations

import copy
import re

# Fixed source executed by the owned renderer. No page expression or model code
# is evaluated. Password values and file bytes never leave the renderer here.
OBSERVE = r'''() => {
  document.querySelectorAll('[data-vool-live]').forEach(e => e.removeAttribute('data-vool-live'));
  const elements=[...document.querySelectorAll('a,button,input,textarea,select')]
    .filter(e=>e.getClientRects().length && getComputedStyle(e).visibility!=='hidden').slice(0,100);
  const references=[...new Set(elements.map(e=>e.form).filter(Boolean))];
  const forms=references.map(f=>{
    const controls=[...f.elements].slice(0,201);
    const metadata=controls.map(e=>({name:e.name||'',id:e.id||'',autocomplete:e.autocomplete||'',type:e.type||''}));
    const secret=new Set(controls.filter(e=>e.type==='password').map(e=>e.name));
    const result={fields:[],metadata,reviewable:f.elements.length<=200,password:secret.size>0,
      payment:!!f.closest('[data-payment]')||!!f.querySelector('[data-payment]')};
    if(!result.reviewable)return result;
    let size=0;
    for(const [name,value] of new FormData(f)) {
      const field={name,value:secret.has(name)?'••••••':typeof value==='string'?value:value.name,
        secret:secret.has(name),file:typeof value!=='string',bytes:typeof value==='string'?0:value.size};
      size+=name.length+field.value.length;
      if(result.fields.length>=200 || name.length>512 || field.value.length>10000 || size>64000) {
        result.reviewable=false;result.fields=[];break;
      }
      result.fields.push(field);
    }
    return result;
  });
  const nodes=elements.map((e,i)=>{
    e.setAttribute('data-vool-live',String(i));const f=e.form, form=f?references.indexOf(f):-1;
    const method=f?(e.getAttribute('formmethod')||f.method||'GET').toUpperCase():'GET';
    let target=e.href||(f?new URL(e.getAttribute('formaction')||f.action,document.baseURI).href:location.href);
    if(f && method==='GET' && forms[form].reviewable && !forms[form].password) {
      const address=new URL(target),fields=new URLSearchParams();
      for(const field of forms[form].fields)fields.append(field.name,field.value);
      if(e.type==='submit' && e.name)fields.append(e.name,e.value);
      address.search=fields.toString();target=address.href;
    }
    return {selector:'[data-vool-live="'+i+'"]',tag:e.tagName.toLowerCase(),name:e.name||'',form,
      label:(e.innerText||e.getAttribute('aria-label')||e.getAttribute('name')||e.type||'').slice(0,120),
      type:e.type||'',navigates:!!e.href||!!f&&e.type==='submit',submit_value:e.type==='submit'?e.value:'',
      target:target.slice(0,4096),target_too_long:target.length>4096,method,
      password_form:!!f&&forms[form].password,
      options:e.tagName==='SELECT'?[...e.options].slice(0,100).map(o=>({value:o.value,label:o.label.slice(0,120)})):[],
      // The control's current state, so a reader can see what a fill already did.
      // Password values and file contents never leave the renderer.
      ...(['INPUT','TEXTAREA','SELECT'].includes(e.tagName)&&!['password','file','submit','button','reset','image'].includes(e.type)
        ?(['checkbox','radio'].includes(e.type)?{checked:e.checked}:{value:String(e.value||'').slice(0,2000),value_truncated:String(e.value||'').length>2000})
        :{}),
      payment:!!e.closest('[data-payment]')};
  });
  return {nodes,forms};
}'''


def payment_form(form: dict) -> bool:
    """Reuse the existing card-field refusal; recognize standard payment controls."""
    from core.vool_browser.shopping import card_like_keys

    if form.get('payment'):
        return True
    metadata = list(form.get('metadata') or [])
    names = [str(value) for control in metadata for key, value in control.items() if key in {'name', 'id'} and value]
    if card_like_keys(dict.fromkeys(names)):
        return True
    if any(str(control.get('autocomplete') or '').split()[-1].startswith(('cc-', 'transaction-'))
           for control in metadata if str(control.get('autocomplete') or '').split()):
        return True
    normalized = {re.sub(r'[^a-z0-9]+', '_', name.casefold()).strip('_') for name in names}
    return bool(normalized & {'amount', 'amount_minor', 'total', 'price', 'charge', 'payment_amount'}
                and normalized & {'currency', 'currency_code', 'payment_currency'})


def submission_preview(form: dict, node: dict, args: dict) -> list[dict]:
    if not form.get('reviewable'):
        raise ValueError('unreviewable_form')
    fields = copy.deepcopy(form['fields'])
    if args.get('submit') and node.get('name'):
        selected = [field for field in fields if field['name'] == node['name']]
        if len(selected) != 1:
            raise ValueError('ambiguous_form_field')
        selected[0]['value'] = '••••••' if selected[0]['secret'] else str(args.get('text') or '')
    if node.get('type') == 'submit' and node.get('name'):
        fields.append({'name':node['name'],'value':node.get('submit_value',''),'secret':False,'file':False,'bytes':0})
    return fields
