"""Bounded form observations and owner-only submission review."""
from __future__ import annotations

import copy
import re

# Fixed source executed by the owned renderer. No page expression or model code
# is evaluated. Password values and file bytes never leave the renderer here.
OBSERVE = r'''() => {
  document.querySelectorAll('[data-vool-live]').forEach(e => e.removeAttribute('data-vool-live'));
  // A control named "action", "method", "elements" or "closest" shadows the form's own
  // property, so form state is read through the prototype, never the element.
  const F=HTMLFormElement.prototype, own=(f,p)=>Object.getOwnPropertyDescriptor(F,p).get.call(f);
  const within=(e,s)=>Element.prototype.closest.call(e,s), has=(f,s)=>Element.prototype.querySelector.call(f,s);
  const submits=e=>(e.tagName==='BUTTON'&&e.type==='submit')||(e.tagName==='INPUT'&&['submit','image'].includes(e.type));
  // Values a person keeps to themselves: never shown to a reader of the observation.
  const private_=e=>e.type==='password'||!!within(e,'[data-payment]')
    ||String(e.autocomplete||'').split(/\s+/).some(t=>/^(cc-|transaction-)|^(one-time-code|current-password|new-password)$/.test(t));
  const elements=[...document.querySelectorAll('a,button,input,textarea,select')]
    .filter(e=>e.getClientRects().length && getComputedStyle(e).visibility!=='hidden').slice(0,100);
  const references=[...new Set(elements.map(e=>e.form).filter(Boolean))];
  const forms=references.map(f=>{
    const controls=[...own(f,'elements')].slice(0,201);
    // The form's default button in tree order. Image buttons are not in form.elements.
    const submitter=[...document.querySelectorAll('button,input')].find(e=>e.form===f&&submits(e));
    const metadata=controls.map(e=>({name:e.name||'',id:e.id||'',autocomplete:e.autocomplete||'',type:e.type||''}));
    const secret=new Set(controls.filter(e=>e.type==='password').map(e=>e.name));
    const result={fields:[],metadata,reviewable:own(f,'elements').length<=200,password:secret.size>0,
      payment:!!within(f,'[data-payment]')||!!has(f,'[data-payment]'),
      default_submit:submitter?{name:submitter.name||'',value:submitter.type==='image'?'':(submitter.value||''),image:submitter.type==='image'}:null};
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
    const method=f?(e.getAttribute('formmethod')||own(f,'method')||'GET').toUpperCase():'GET';
    let target=e.href||(f?new URL(e.getAttribute('formaction')||own(f,'action'),document.baseURI).href:location.href);
    if(f && method==='GET' && forms[form].reviewable && !forms[form].password) {
      const address=new URL(target),fields=new URLSearchParams();
      for(const field of forms[form].fields)fields.append(field.name,field.value);
      if(submits(e) && e.type!=='image' && e.name)fields.append(e.name,e.value);
      address.search=fields.toString();target=address.href;
    }
    return {selector:'[data-vool-live="'+i+'"]',tag:e.tagName.toLowerCase(),name:e.name||'',form,
      label:(e.innerText||e.getAttribute('aria-label')||e.getAttribute('name')||e.type||'').slice(0,120),
      type:e.type||'',navigates:!!e.href||!!f&&submits(e),submit_value:submits(e)&&e.type!=='image'?e.value:'',
      target:target.slice(0,4096),target_too_long:target.length>4096,method,
      password_form:!!f&&forms[form].password,
      options:e.tagName==='SELECT'?[...e.options].slice(0,100).map(o=>({value:o.value,label:o.label.slice(0,120)})):[],
      // The control's current state, so a reader can see what a fill already did.
      // Password, card, one-time-code and payment values and file contents never leave the renderer.
      ...(['INPUT','TEXTAREA','SELECT'].includes(e.tagName)&&!['password','file','submit','button','reset','image'].includes(e.type)&&!private_(e)
        ?(['checkbox','radio'].includes(e.type)?{checked:e.checked}:{value:String(e.value||'').slice(0,2000),value_truncated:String(e.value||'').length>2000})
        :{}),
      payment:!!within(e,'[data-payment]')};
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
    # recipientIban, paymentAmount: camelCase words split like snake_case ones.
    normalized = {re.sub(r'[^a-z0-9]+', '_', re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', name).casefold()).strip('_') for name in names}
    tokens = {token for name in normalized for token in name.split('_')}
    # Bank transfers and card numbers by their usual field names, and any amount field: a form
    # that moves money goes through the wallet, so an unrecognised one is refused, not reviewed.
    if tokens & {'iban', 'bic', 'swift', 'routing', 'ccnum', 'ccnumber', 'cvv', 'cvc', 'amount'}:
        return True
    if normalized & {'account_number', 'accountnumber', 'bank_account', 'sort_code', 'sortcode', 'cc_number', 'cc_num'}:
        return True
    return bool(normalized & {'total', 'price', 'charge'}
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
    # The button that sends the form is part of what is sent: the clicked one, or for a fill
    # sent with Enter the form's first submit button, exactly as the browser picks it.
    submitter = {'name': node.get('name') or '', 'value': node.get('submit_value', ''), 'image': node.get('type') == 'image'} \
        if node.get('navigates') and node.get('form', -1) >= 0 and not args.get('submit') else form.get('default_submit')
    if submitter and submitter.get('image'):
        raise ValueError('image_submit')
    if submitter and submitter.get('name'):
        fields.append({'name':submitter['name'],'value':submitter.get('value',''),'secret':False,'file':False,'bytes':0})
    return fields
