"""Plugin controls rendered inside the existing Add-ons inventory and review flow."""


def plugin_functions() -> str:
    return r'''
let pluginReview=null,pluginConsent=null,pluginRemoval=null,pluginNotice='';
async function pluginPost(body){const result=await post(body);[data,_pluginCatalog]=await Promise.all([read(),fetch('/api/plugins').then(r=>r.json())]);pluginReview=null;pluginConsent=null;pluginRemoval=null;pluginNotice='';return result;}
function pluginFacts(host,p){
 if(((p.tools||[]).length||(p.mcp_servers||[]).length)&&p.executable_runtime_available===false)host.append(node('p',tr(p.executable_runtime_refusal_key,p.executable_runtime_refusal)));
 if(((p.tools||[]).length||(p.mcp_servers||[]).length)&&p.executable_runtime_available!==false&&p.mac_process_note)host.append(node('p',tr('plugin_mac_process_note','On a Mac, a plugin’s programs can see some settings of other apps you run, like any app can. Your plugin keys are never put there.')));
 if((p.kind||[]).includes('model_provider'))host.append(node('p',tr('plugin_provider_effect','Sends the chat requests you choose to this service. Usage may be billed to your service account.')));
 if((p.kind||[]).includes('scanner'))host.append(node('p',tr('plugin_scanner_effect','Sends public add-on content to this service when you request a scan. A scan does not grant installation or tool permissions.')));
 const effects={read_only:tr('plugin_effect_read','Read information'),workspace_write:tr('plugin_effect_write','Change files in its own working space'),network_send:tr('plugin_effect_send','Send information'),network_publish:tr('plugin_effect_publish','Publish information'),external_unpinned:tr('plugin_effect_unknown','VOOL can’t tell what this does, so it asks you every time')};
 const list=node('ul');(p.tools||[]).forEach(t=>list.append(node('li',t.description+' · '+(effects[t.side_effect_class]||tr('plugin_effect_other','Uses the normal permission checks')))));host.append(list);
 host.append(node('h4',tr('plugin_network','Network access')));
 host.append(node('p',(p.network||[]).length?p.network.join(', '):tr('plugin_no_network','No network access.')));
 if((p.network||[]).length&&((p.tools||[]).length||(p.mcp_servers||[]).length)&&p.process_network_available===false)host.append(node('p',tr('plugin_network_unavailable','This computer can’t limit which sites the plugin reaches, so its internet access stays off. Its tools still work.')));
 host.append(node('h4',tr('plugin_keys','Service keys')));
 const slots=Object.values(p.credentials||{});host.append(node('p',slots.length?slots.map(s=>s.label).join(', '):tr('plugin_no_keys','No service keys requested.')));
 if((p.mcp_servers||[]).length)host.append(node('p',tr('plugin_local_server','Runs a small helper program on this computer.')));
}
function renderServicePlugins(host,query){
 hero(host,tr('plugin_title','Plugins'),tr('plugin_intro','Add tools and services you choose. Installing keeps a plugin disabled until you review its access.'));
 (data?.plugin_notices||[]).forEach(n=>{const notice=node('div',undefined,'addon-meta');notice.append(node('p',tr(n.message_key,n.message)));if(n.dismissible)notice.append(button(tr('plugin_notice_dismiss','Got it'),()=>action(async()=>{await pluginPost({action:'plugin_notice_dismiss'});})));host.append(notice);});
 if(pluginNotice){const notice=node('p',pluginNotice);notice.setAttribute('role','status');host.append(notice);}
 if(pluginReview){
  const p=pluginReview.plugin;const card=node('section',undefined,'addon-card');card.setAttribute('role','dialog');card.setAttribute('aria-label',tr('plugin_review','Review plugin package'));card.append(node('h3',p.name),node('p',p.description),node('p',tr('plugin_not_reviewed','Not reviewed by the VOOL store')),node('p',tr('plugin_review_full','VOOL checks these files haven’t changed. That does not prove they are safe.')));pluginFacts(card,p);
  card.append(button(tr('plugin_install_disabled','Install and keep disabled'),()=>action(async()=>{await pluginPost({action:'plugin_install',review_id:pluginReview.review_id,digest:pluginReview.digest,accepted:true});}),'addon-primary'),button(tr('plugin_cancel','Cancel'),()=>{pluginReview=null;refresh();}));host.append(card);return;
 }
 if(pluginConsent){
  const p=pluginConsent;const card=node('section',undefined,'addon-card');card.setAttribute('role','dialog');card.setAttribute('aria-label',tr('plugin_access_review','Review plugin access'));card.append(node('h3',p.name),node('p',tr('plugin_consent_intro','Enabling lets this plugin use the access listed below. Your normal permission checks still apply.')));pluginFacts(card,p);
  card.append(button(tr('plugin_accept_enable','Accept and enable'),()=>action(async()=>{await pluginPost({action:'plugin_enable',id:p.id,accepted:true,approval_digest:p.approval_digest});}),'addon-primary'),button(tr('plugin_keep_disabled','Keep disabled'),()=>{pluginConsent=null;refresh();}));host.append(card);return;
 }
 if(pluginRemoval){
  const p=pluginRemoval;const card=node('section',undefined,'addon-card');card.setAttribute('role','dialog');card.setAttribute('aria-label',tr('plugin_remove_review','Uninstall plugin'));card.append(node('h3',p.name),node('p',tr('plugin_remove_intro','The plugin will be disabled before its files and working space are removed. Its installation history is kept.')));
  const label=node('label');const checkbox=node('input');checkbox.type='checkbox';checkbox.checked=true;label.append(checkbox,document.createTextNode(tr('plugin_remove_keys','Also remove saved keys')));card.append(label);
  card.append(button(tr('plugin_uninstall','Uninstall'),()=>action(async()=>{await pluginPost({action:'plugin_uninstall',id:p.id,remove_key:checkbox.checked});})),button(tr('plugin_cancel','Cancel'),()=>{pluginRemoval=null;refresh();}));host.append(card);return;
 }
 const install=node('section',undefined,'addon-card');install.append(node('h3',tr('plugin_add_folder','Install from a folder')));
 const label=node('label',tr('plugin_folder_location','Plugin folder location'));const folder=node('input');folder.type='text';folder.id='pluginLocalFolder';folder.autocomplete='off';label.htmlFor=folder.id;install.append(label,folder);
 const replaceLabel=node('label');const replace=node('input');replace.type='checkbox';replaceLabel.append(replace,document.createTextNode(tr('plugin_replace','Review an update to an installed plugin')));install.append(replaceLabel);
 install.append(button(tr('plugin_review_folder','Review folder'),()=>action(async()=>{pluginReview=await post({action:'plugin_prepare',path:folder.value,update:replace.checked});})));host.append(install);
 const github=node('details',undefined,'addon-card');github.append(node('summary',tr('plugin_add_github','Install from public GitHub source')));
 const fields={};[['repository','plugin_repository','GitHub project (owner/name)'],['revision','plugin_revision','Version to check'],['path','plugin_repository_folder','Folder inside the project']].forEach(([id,key,fallback])=>{const label=node('label',tr(key,fallback));const input=node('input');input.type='text';input.id='pluginGitHub_'+id;input.autocomplete='off';label.htmlFor=input.id;fields[id]=input;github.append(label,input);});
 const downloadLabel=node('label');const download=node('input');download.type='checkbox';downloadLabel.append(download,document.createTextNode(tr('plugin_download_consent','Download the public package for review and check its contents on this device')));github.append(downloadLabel);
 github.append(button(tr('plugin_review_source','Download and review'),()=>action(async()=>{pluginReview=await post({action:'plugin_github_prepare',repository:fields.repository.value,revision:fields.revision.value,path:fields.path.value,approved:download.checked,update:replace.checked});})));host.append(github);
 const grid=node('div',undefined,'addon-grid');host.append(grid);
 const rows=(data?.plugins||[]).filter(p=>key([p.name,p.description,p.publisher].join(' ')).includes(key(query)));
 if(!rows.length)grid.append(node('p',tr('plugin_empty','No plugins installed.')));
 rows.forEach(p=>{
  const card=node('article',undefined,'addon-card');card.dataset.pluginId=p.id;card.append(node('h3',p.name),node('p',p.description||''),node('p',p.enabled?tr('plugin_enabled','Enabled'):tr('plugin_disabled','Disabled'),'addon-state'));
  if(!p.reviewed_by_store)card.append(node('p',tr('plugin_not_reviewed','Not reviewed by the VOOL store'),'addon-meta'));
  if(p.reason)card.append(node('p',tr(p.reason_key,p.reason),'addon-error'));
  if((p.kind||[]).includes('model_provider'))card.append(node('p',tr('plugin_model_count','{count} models available').replace('{count}',String(p.model_count||0))));
  if(p.enabled&&(p.kind||[]).some(k=>k==='scanner'||k==='model_provider'))card.append(button(tr('plugin_test_connection','Test connection'),()=>action(async()=>{await pluginPost({action:'plugin_test',id:p.id});pluginNotice=tr('plugin_connection_checked','Connection checked. The service returned a response.');})));
  if(p.enabled&&(p.kind||[]).includes('model_provider'))card.append(button(tr('plugin_refresh_models','Refresh model list'),()=>action(async()=>{await pluginPost({action:'plugin_models',id:p.id});pluginNotice=tr('plugin_models_refreshed','Prices come from the service. Your spending limits still apply.');})));
  const actions=node('div',undefined,'addon-actions');if(p.stage!=='invalid')actions.append(p.enabled?button(tr('plugin_disable','Disable'),()=>action(async()=>{await pluginPost({action:'plugin_disable',id:p.id});})):button(tr('plugin_enable','Review access and enable'),()=>{pluginConsent=p;refresh();}));actions.append(button(tr('plugin_uninstall','Uninstall'),()=>{pluginRemoval=p;refresh();}));card.append(actions);
  Object.entries(p.credentials||{}).forEach(([slot,spec])=>{const label=node('label',spec.label);const input=node('input');input.type='password';input.autocomplete='new-password';input.id='pluginKey_'+p.id+'_'+slot;label.htmlFor=input.id;card.append(label,input);if((p.configured_slots||[]).includes(slot))card.append(node('p',tr('plugin_key_saved','Key saved.')));card.append(button(tr('plugin_save_key','Save key'),()=>action(async()=>{const value=input.value;input.value='';await pluginPost({action:'plugin_connect',id:p.id,slot,value});})));if(spec.help_url)card.append(link(tr('plugin_get_key','Get a service key'),spec.help_url));});grid.append(card);
 });
}
'''
