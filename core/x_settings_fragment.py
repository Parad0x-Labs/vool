"""X feature access and cost-reservation controls inside the existing Settings page."""

X_FEATURES_JS = r"""
function xMoneySymbols() {
  const locale = document.documentElement.lang || 'en';
  const options = { useGrouping: false };
  if (locale.split('-')[0] === 'ar') options.numberingSystem = 'arab';
  const format = new Intl.NumberFormat(locale, options);
  return { digits: Array.from({ length: 10 }, (_, digit) => format.format(digit)),
    decimal: format.formatToParts(1.1).find(part => part.type === 'decimal')?.value || '.' };
}
function xMicroAmount(value) {
  const symbols = xMoneySymbols();
  let text = String(value).trim();
  for (let digit = 0; digit < 10; digit++) text = text.split(symbols.digits[digit]).join(String(digit));
  if (symbols.decimal !== '.') {
    if (text.includes('.')) throw new Error('amount');
    text = text.split(symbols.decimal).join('.');
  }
  if (!/^[0-9]+(?:\.[0-9]{1,6})?$/.test(text)) throw new Error('amount');
  const parts = text.split('.');
  const amount = BigInt(parts[0]) * 1000000n + BigInt((parts[1] || '').padEnd(6, '0'));
  if (amount > 1000000000000n) throw new Error('amount');
  return Number(amount);
}
function xAmountText(value) {
  const amount = Number(value);
  if (!Number.isSafeInteger(amount) || amount < 0) return '';
  const whole = Math.floor(amount / 1000000);
  const fraction = String(amount % 1000000).padStart(6, '0').replace(/0+$/, '');
  const symbols = xMoneySymbols();
  return (String(whole) + (fraction ? symbols.decimal + fraction : '')).replace(/[0-9]/g, digit => symbols.digits[Number(digit)]);
}
function xApiProposalDetails(card, action) {
  const target = action.target || {};
  card.appendChild(el('p', 'subtle', tfmt('xfull.ceiling', 'Maximum reservation: {amount} USD',
    { amount: xAmountText(target.ceiling_microusd || 0) })));
  const details = document.createElement('details');
  details.appendChild(el('summary', null, T('xfull.request_details', 'Exact request details')));
  if (action.request_unavailable) {
    card.appendChild(el('p', 'subtle x-api-request-unavailable', T('xfull.request_unavailable', "This request could not be read, so it can't be sent. Decline it and ask again.")));
    return;
  }
  const body = el('pre', 'x-api-request-details', action.text || '');
  body.style.whiteSpace = 'pre-wrap'; body.style.overflowWrap = 'anywhere';
  details.appendChild(body); card.appendChild(details);
}
function xApiActionMessage(out, fallbackAction) {
  const action = out.j?.action || fallbackAction || {};
  const state = String(out.j?.state || action.state || '');
  if (state === 'declined') return T('xact.state_declined', 'Declined');
  if (state === 'expired') return T('xact.state_expired', 'Expired');
  if (state === 'unknown' || state === 'outcome_unknown') return T('xact.lost_answer', 'VOOL did not get an answer. It may already have been sent: check Recent below and X before trying again.');
  if (out.ok || state === 'sent') return T('xact.state_sent', 'Sent');
  if (state === 'failed') return T('xfull.request_failed', 'The X request failed. Check its receipt.');
  if (state === 'request_unavailable') return T('xfull.request_unavailable', "This request could not be read, so it can't be sent. Decline it and ask again.");
  if (['spend_approval_required', 'spend_cap_exhausted'].includes(state)) return T('xfull.reservation_refused', 'The cost reservation was refused. Check your wallet approval and spending limits.');
  if (['quota_exhausted', 'daily_limit_reached', 'usage_limit_reached', 'quota_unavailable'].includes(state)) return T('xfull.call_limit', 'The call limit was reached. Review your X limits before trying again.');
  if (['configuration_changed', 'account_changed', 'disabled', 'not_in_access_profile'].includes(state)) return T('xfull.profile_changed', 'Your X features, limits or account changed. Reload and review a new request.');
  return T('xfull.not_confirmed', 'The request was not confirmed. Reload activity and check its receipt before trying again.');
}
function xApiReceiptDetails(host, action) {
  const receipt = action.result?.receipt;
  if (!receipt) return;
  const details = document.createElement('details');
  details.appendChild(el('summary', null, T('xfull.receipt', 'Call receipt')));
  details.appendChild(el('p', 'subtle', tfmt('xfull.ceiling', 'Maximum reservation: {amount} USD',
    { amount: xAmountText(receipt.ceiling_microusd || 0) })));
  details.appendChild(el('p', 'subtle', new Date(receipt.created_at * 1000).toLocaleString(document.documentElement.lang || 'en')));
  details.appendChild(el('code', null, receipt.id));
  if (receipt.ceiling_microusd > 0 && receipt.state !== 'unsent') details.appendChild(el('p', 'subtle',
    T('xfull.billing_note', 'X bills your developer account. Paid reservations stay held until billing is reconciled. Daily and monthly limits include other spending recorded by VOOL.')));
  host.appendChild(details);
}
function xApiCleanupControl(host, action, redraw) {
  if (!action.cleanup_pending) return;
  host.appendChild(el('p', 'subtle', T('xfull.cleanup_pending', 'The private request copy has not been erased.')));
  const button = el('button', 'btn', T('xfull.cleanup_retry', 'Retry private-copy cleanup')); button.type = 'button';
  button.addEventListener('click', async () => {
    button.disabled = true;
    try { await xActivityCall({ action: 'cleanup_request', action_id: action.id }); } catch (e) { /* redraw retains the failure */ }
    await redraw('');
  });
  host.appendChild(button);
}
async function widgetXFeatures(host) {
  const note = el('p', 'subtle'); note.setAttribute('role', 'status'); note.setAttribute('aria-live', 'polite');
  const call = async (body) => {
    const response = await fetch('/api/x/capabilities', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...body, ui_locale: document.documentElement.lang || 'en' }) });
    return { ok: response.ok, data: await response.json() };
  };
  let response;
  try { response = await call({ action: 'overview' }); } catch (e) { response = { ok: false }; }
  host.replaceChildren();
  host.appendChild(el('h4', 'row-label', T('xfull.title', 'X features and spending')));
  if (!response.ok) {
    host.appendChild(el('p', 'subtle', T('xfull.load_failed', 'Could not load X features.'))); return;
  }
  const config = response.data.configuration;
  const features = response.data.features || [];
  const savedState = el('p', 'subtle x-features-saved-state', config.enabled
    ? T('xfull.on', 'Calls are on for your saved features.') : T('xfull.off', 'X calls are off.'));
  host.appendChild(savedState);
  const form = el('div', 'stack x-features-form'); host.appendChild(form);
  const enabledLabel = el('label', 'inline');
  const enabled = document.createElement('input'); enabled.type = 'checkbox'; enabled.checked = config.enabled;
  enabled.className = 'x-features-enabled'; enabledLabel.appendChild(enabled);
  enabledLabel.appendChild(document.createTextNode(T('xfull.enable', 'Allow the selected X features')));
  form.appendChild(enabledLabel);
  const named = el('label', 'stack'); named.appendChild(el('span', null, T('xfull.access', 'Your X access profile')));
  const access = el('input', 'inp x-features-access'); access.type = 'text'; access.maxLength = 80;
  access.value = config.access_label || ''; named.appendChild(access); form.appendChild(named);
  form.appendChild(el('p', 'subtle', T('xfull.access_note', 'Select only features your X access allows. Unselected features cannot run.')));
  form.appendChild(el('p', 'subtle', T('xfull.price_note', 'Use the highest cost a call can incur. A reservation is a limit, not proof of the final charge.')));
  form.appendChild(el('p', 'subtle', T('xfull.free_note', 'Enter zero only when your X access makes the feature free.')));
  const limits = el('div', 'x-limits');
  const limitFields = {};
  const makeLimit = (name, key, words) => {
    const label = el('label', 'stack'); label.appendChild(el('span', 'subtle', T(key, words)));
    const input = el('input', 'inp'); input.type = 'text'; input.inputMode = 'decimal'; input.value = xAmountText(config.limits[name]);
    input.placeholder = xAmountText(10000);
    input.dataset.xLimit = name; label.appendChild(input); limits.appendChild(label); limitFields[name] = input;
  };
  makeLimit('per_call_microusd', 'xfull.call_cap', 'Maximum per call (USD)');
  makeLimit('per_task_microusd', 'xfull.chat_cap', 'Maximum per chat (USD)');
  makeLimit('daily_microusd', 'xfull.day_cap', 'Maximum per day (USD)');
  makeLimit('monthly_microusd', 'xfull.month_cap', 'Maximum per month (USD)');
  form.appendChild(limits);
  form.appendChild(el('strong', null, T('xfull.select', 'Allowed features')));
  const search = el('input', 'inp x-features-search'); search.type = 'search';
  search.setAttribute('aria-label', T('xfull.search', 'Find a feature')); search.placeholder = T('xfull.search', 'Find a feature');
  form.appendChild(search);
  const selected = new Map(Object.entries(config.operations || {}).map(([key, value]) => [key, xAmountText(value)]));
  const list = el('div', 'stack x-features-list'); list.style.maxHeight = '24rem'; list.style.overflowY = 'auto';
  form.appendChild(list);
  const draw = () => {
    list.replaceChildren();
    const query = search.value.toLocaleLowerCase();
    features.filter(feature => (T('xop.' + feature.id, feature.summary) + ' ' + feature.summary + ' ' + feature.id + ' ' + feature.path).toLocaleLowerCase().includes(query))
      .forEach(feature => {
        const row = el('div', 'x-feature-row'); row.dataset.operationId = feature.id;
        const choice = el('label', 'inline');
        const check = document.createElement('input'); check.type = 'checkbox'; check.checked = selected.has(feature.id);
        check.disabled = Boolean(feature.deferred); choice.appendChild(check);
        choice.appendChild(document.createTextNode(T('xop.' + feature.id, feature.summary))); row.appendChild(choice);
        if (feature.deferred) row.appendChild(el('span', 'subtle', T('xfull.extra_permission', 'Needs an extra X permission that the sign-in does not ask for yet.')));
        else {
          const label = el('label', 'stack'); label.appendChild(el('span', 'subtle', T('xfull.feature_cost', 'Reserve per call (USD)')));
          const price = el('input', 'inp x-feature-price'); price.type = 'text'; price.inputMode = 'decimal';
          price.placeholder = xAmountText(10000);
          price.value = selected.get(feature.id) ?? ''; price.disabled = !check.checked;
          label.appendChild(price); row.appendChild(label);
          check.addEventListener('change', () => {
            price.disabled = !check.checked;
            if (check.checked) { selected.set(feature.id, price.value); price.focus(); } else selected.delete(feature.id);
          });
          price.addEventListener('input', () => { if (check.checked) selected.set(feature.id, price.value); });
        }
        list.appendChild(row);
      });
    if (!list.childElementCount) list.appendChild(el('p', 'subtle', T('xfull.no_features', 'No matching features.')));
  };
  search.addEventListener('input', draw); draw();
  const actions = el('div', 'inline');
  const save = el('button', 'btn primary x-features-save', T('xfull.save', 'Save features and limits')); save.type = 'button';
  const off = el('button', 'btn x-features-off', T('xfull.disable_now', 'Turn calls off')); off.type = 'button'; off.disabled = !config.enabled;
  const reload = el('button', 'btn x-features-reload', T('xfull.reload', 'Reload saved settings')); reload.type = 'button';
  reload.addEventListener('click', () => widgetXFeatures(host));
  actions.appendChild(save); actions.appendChild(off); actions.appendChild(reload); form.appendChild(actions);
  form.appendChild(el('p', 'subtle', T('xfull.device_note', 'Device approval is required to enable or change active features.')));
  form.appendChild(el('p', 'subtle', T('xfull.billing_note', 'X bills your developer account. Paid reservations stay held until billing is reconciled. Daily and monthly limits include other spending recorded by VOOL.')));
  host.appendChild(note);
  const submit = async (candidate) => {
    const controls = [...form.querySelectorAll('input,button')]; controls.forEach(input => { input.disabled = true; });
    let out;
    try { out = await call({ action: 'save', configuration: candidate,
      expected_fingerprint: response.data.configuration_fingerprint }); } catch (e) { out = { ok: false, data: {} }; }
    if (out.ok) {
      await widgetXFeatures(host);
      const done = el('p', 'subtle x-features-result', candidate.enabled
        ? T('xfull.saved', 'Features and limits saved.') : T('xfull.off_saved', 'X calls are now off.'));
      done.setAttribute('role', 'status'); host.appendChild(done);
    } else {
      controls.forEach(input => { input.disabled = false; }); draw(); off.disabled = !config.enabled;
      note.textContent = out.data?.state === 'approval_refused'
        ? T('xfull.device_declined', 'Device approval was declined or unavailable.')
        : T('xfull.save_failed', 'These settings were not confirmed. Reload before trying again.');
    }
  };
  save.addEventListener('click', () => {
    try {
      const candidate = { ...config, enabled: enabled.checked, access_label: access.value.trim(), operations: {}, limits: {} };
      selected.forEach((value, key) => { candidate.operations[key] = xMicroAmount(value); });
      Object.entries(limitFields).forEach(([key, input]) => { candidate.limits[key] = xMicroAmount(input.value); });
      submit(candidate);
    } catch (e) { note.textContent = T('xfull.invalid_money', 'Use a non-negative amount with up to six decimal places.'); }
  });
  off.addEventListener('click', () => submit({ ...config, enabled: false }));
}
"""
