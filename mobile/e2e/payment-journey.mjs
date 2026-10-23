import assert from "node:assert/strict";
import {SealedMobileCompanionClient} from "../src/protocol/sealed.ts";
import {MemoryProposalStore, PaymentDraft} from "../src/protocol/paymentDraft.ts";
const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const {credentials, destination} = JSON.parse(Buffer.concat(chunks).toString());
const packets = [];
const wire = async (url, init) => {
  packets.push(JSON.parse(init.body));
  const response = await fetch(url, init);
  if (packets.length === 2) {await response.json(); throw new Error("Connection lost after commit");}
  return response;
};
const client = new SealedMobileCompanionClient(credentials, {fetchImpl: wire});
const store = new MemoryProposalStore();
await assert.rejects(new PaymentDraft(store, client, async()=>true).send(destination, "0.009"), /lost after commit/);
const saved = await store.load();
assert.ok(saved?.idempotency_key);
await assert.rejects(new PaymentDraft(store, client, async()=>true).send(destination, "0.008"), /saved payment request/);
assert.equal(packets.length, 2);
const result = await new PaymentDraft(store, client, async()=>true).send(destination, "0.009");
assert.equal(await store.load(), null);
assert.equal(packets.length, 4);
assert.notEqual(packets[0].nonce, packets[1].nonce);
for (const packet of packets) {
  assert.ok(!JSON.stringify(packet).includes(destination));
  assert.ok(!JSON.stringify(packet).includes('"amount"'));
}
console.log(JSON.stringify({proposal_id: result.proposal.proposal_id, attempts: packets.length, store: "explicit headless memory adapter"}));
