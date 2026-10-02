"""Node component proof of observer/claim wiring; actual audio is a platform lane."""
import re

from core.notification_fragment import render_notification_fragment
from tests.chat_page_js_harness import DOM, run_node


def test_locked_observer_cannot_steal_sound_or_replay_after_unlock():
    script=re.findall(r'<script>(.*?)</script>',render_notification_fragment(),re.S)[0]
    result=run_node(DOM+r"""
let cursor=10, ready=false, claims=[], played=0;
window.VoolNotificationAudio={ready:()=>ready,cue:async()=>{played++;}};
globalThis.fetch=async(url,opts)=>{
 if(url==='/api/notifications/audio'){claims.push(JSON.parse(opts.body).after);return {json:async()=>({ok:true,notification_ids:['fresh']})};}
 return {json:async()=>({ok:true,items:[],unread:0,cursor})};
};
"""+script+r"""
await window.VoolNotify.refresh();
cursor=11;await window.VoolNotify.refresh();
if(claims.length)throw Error('locked observer claimed audio');
ready=true;await window.VoolNotify.refresh();
if(claims.length)throw Error('unlock replayed history');
cursor=12;await window.VoolNotify.refresh();
await window.VoolNotify.refresh();
out({claims,played,errors});
""")
    assert result['errors']==[]
    assert result['claims']==[11] and result['played']==1
