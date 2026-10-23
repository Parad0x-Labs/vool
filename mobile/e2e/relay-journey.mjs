import assert from 'node:assert/strict';
import {SealedMobileCompanionClient} from '../src/protocol/sealed.ts';
const chunks=[]; for await (const chunk of process.stdin) chunks.push(chunk);
const {credentials}=JSON.parse(Buffer.concat(chunks).toString());
const packets=[]; let savedResponse;
const wire=async (url,init)=> {
  packets.push(JSON.parse(init.body));
  const response=await fetch(url,init);
  if(packets.length===1) {savedResponse=await response.json(); throw new Error('Dropped after response');}
  return response;
};
const client=new SealedMobileCompanionClient(credentials,{fetchImpl:wire});
await assert.rejects(client.devicesMe(), /Dropped after response/);
assert.equal((await client.devicesMe()).device_id,credentials.deviceId);
assert.notEqual(packets[0].nonce,packets[1].nonce);
for(const value of [...packets,savedResponse]) {
 const text=JSON.stringify(value);
 assert.ok(!text.includes(credentials.grantSecret)); assert.ok(!text.includes('Test phone'));
 assert.ok(!text.includes('devices.me')); assert.ok(!text.includes('rules'));
}
// Replay and tamper are sent by the path observer, without a grant or signing key.
const replay=await fetch(credentials.baseUrl+'/api/mobile/companion',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(packets[0])});
assert.equal(replay.status,409);
const tamper={...packets[1],ciphertext:(packets[1].ciphertext[0]==='A'?'B':'A')+packets[1].ciphertext.slice(1)};
const corrupted=await fetch(credentials.baseUrl+'/api/mobile/companion',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(tamper)});
assert.equal(corrupted.status,503);
console.log(JSON.stringify({requests:packets.length,replay:replay.status,tamper:corrupted.status}));
