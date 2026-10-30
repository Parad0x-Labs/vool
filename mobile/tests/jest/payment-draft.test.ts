import {PaymentDraft, MemoryProposalStore} from '../../src/protocol/paymentDraft';

test('an old payment reply cannot erase a newer saved intent after a screen remount', async () => {
  const store = new MemoryProposalStore();
  let finish!: (value: unknown) => void;
  const previewPayment=async()=>({review_token:'review',amount:'0.01',asset:'SOL',network:'solana-mainnet',destination:'payee-one',warnings:[],expires_at:Date.now()/1000+300});
  const first = new PaymentDraft(store, {previewPayment,proposePayment: () => new Promise(resolve => {finish = resolve;})},async()=>true);
  const waiting = first.send('payee-one', '0.01');
  for (let n=0; n<15; n++) await Promise.resolve();
  const old = await store.load(); expect(old).not.toBeNull();
  const remounted = new PaymentDraft(store, {previewPayment,proposePayment: async () => ({})},async()=>true);
  await remounted.discard();
  await store.save({destination:'payee-two', amount:'0.02', idempotency_key:'new-intent'});
  finish({proposal_id:'old-proposal'}); await waiting;
  expect((await store.load())?.idempotency_key).toBe('new-intent');
});
