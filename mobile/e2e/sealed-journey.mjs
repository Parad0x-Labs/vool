// The shared phone transport against a running desktop and its separate socket.
import assert from "node:assert/strict";
import {fetchPairingChallenge} from "../src/protocol/client.ts";
import {generateDeviceKeypair} from "../src/protocol/ed25519.ts";
import {claimSealedPairing, SealedMobileCompanionClient} from "../src/protocol/sealed.ts";

const desktop = process.env.VOOL_DESKTOP_URL;
const phone = process.env.VOOL_PHONE_URL;
assert.ok(desktop && phone);
const post = async (base, path, body) => {
  const r=await fetch(base+path,{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify(body)});
  return {status:r.status,body:await r.json()};
};
const start=await post(desktop,"/api/mobile/pairing/start",{});
assert.equal(start.status,200);
const challenge=await fetchPairingChallenge(phone,start.body.pairing_id);
const key=generateDeviceKeypair();
const args={pairingId:start.body.pairing_id,code:start.body.code,deviceName:"Sealed journey phone",platform:"android",
  deviceSeedHex:key.seedHex,devicePublicKeyHex:key.publicKeyHex,challenge,pinnedDesktopFingerprint:start.body.desktop_fingerprint};
await assert.rejects(()=>claimSealedPairing(phone,{...args,pinnedDesktopFingerprint:"0".repeat(64)}));
await assert.rejects(()=>claimSealedPairing(phone,{...args,challenge:{...challenge,sealing:undefined}}));
const credentials=await claimSealedPairing(phone,args);
let lastWire;
const client=new SealedMobileCompanionClient(credentials,{fetchImpl:async(url,init)=>{
  lastWire=JSON.parse(init.body);
  assert.equal(lastWire.action,undefined);
  assert.equal(lastWire.params,undefined);
  assert.ok(!init.body.includes(credentials.grantSecret));
  return fetch(url,init);
}});
assert.equal((await client.devicesMe()).device_id,credentials.deviceId);
const snapshot=await client.statusSnapshot();
assert.equal(snapshot.desktop.fingerprint,credentials.desktopFingerprint);
const replay=await post(phone,"/api/mobile/companion",lastWire);
assert.equal(replay.status,409);
await client.unpair();
await assert.rejects(()=>client.devicesMe(),e=>e.code==="device_revoked");
console.log("sealed journey: pinned pairing, private requests and replies, status, replay refusal, unpair and revoke pass");
