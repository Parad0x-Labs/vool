import assert from 'node:assert/strict';
import {SealedMobileCompanionClient} from '../src/protocol/sealed.ts';
import {Outbox,MemoryOutboxStore} from '../src/protocol/outbox.ts';
const chunks=[];for await(const chunk of process.stdin)chunks.push(chunk);
const {credentials,desktop,relay}=JSON.parse(Buffer.concat(chunks).toString());
let release,arrived;
const gate=new Promise(resolve=>{release=resolve;});const held=new Promise(resolve=>{arrived=resolve;});
const packets=[];
const client=new SealedMobileCompanionClient(credentials,{fetchImpl:async(url,init)=>{
 packets.push({url,nonce:JSON.parse(init.body).nonce});
 const response=await fetch(url,init);
 if(packets.length===1){const raw=await response.text();arrived();await gate;return new Response(raw,{status:response.status});}
 return response;
}});
const store=new MemoryOutboxStore(),turns=[];
const box=new Outbox(store,client,()=>{},result=>turns.push(result.turn_id));
const first=await box.enqueue(null,'The first queued message',[]);
const flushing=box.flush();await held;
await box.enqueue(first.chatId,'The second message while changing connection',[]);
const response=await fetch(desktop+'/api/mobile/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({operation:'relay',...relay})});
assert.equal(response.status,200);
const address=relay.endpoint+'/rooms/'+relay.room;
client.setConnectionAddress(address);release();await flushing;
// The relay fixture intentionally expires a response after two seconds. A
// slow real turn stays queued under the same identity and resumes by retry.
const deadline=Date.now()+25_000;
while((await store.load()).length && Date.now()<deadline){await new Promise(resolve=>setTimeout(resolve,500));await box.flush();}
assert.equal((await store.load()).length,0);assert.equal(turns.length,2);
assert.ok(packets[0].url.startsWith(credentials.baseUrl));assert.ok(packets[1].url.startsWith(address));
assert.equal(new Set(packets.map(packet=>packet.nonce)).size,packets.length);
console.log(JSON.stringify({turn_ids:turns,remaining:0,packets:packets.length}));
