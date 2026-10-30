import {SessionLock} from '../../src/security/session.ts';

describe('private session', () => {
  it('starts closed, keeps failures closed and covers every inactive frame', async () => {
    const state = new SessionLock(() => {});
    expect(state.locked).toBe(true);
    expect(await state.unlock(async () => false)).toBe(false);
    expect(state.locked).toBe(true);
    expect(await state.unlock(async () => true)).toBe(true);
    state.background('inactive');
    expect(state.locked).toBe(true);
    state.background('active');
    expect(state.locked).toBe(true);
  });
  it('expires by inactivity and touches extend only an unlocked session', async () => {
    let now = 100;
    const state = new SessionLock(() => {}, () => now);
    state.touch();
    expect(state.locked).toBe(true);
    await state.unlock(async () => true);
    now += 290000; state.touch(); now += 299999; state.tick();
    expect(state.locked).toBe(false);
    now++; state.tick(); expect(state.locked).toBe(true);
  });
  it('refuses a stale successful authentication across a background trip', async () => {
    const state = new SessionLock(() => {});
    let finish!: (value: boolean) => void;
    const first = state.unlock(() => new Promise(resolve => {finish = resolve;}));
    expect(await state.unlock(async () => true)).toBe(false);
    state.background('background'); state.background('active');
    finish(true); expect(await first).toBe(false); expect(state.locked).toBe(true);
  });
});

import {connectionAddress} from '../../src/protocol/connection.ts';
it('only changes a route, rejects secret URLs and insecure public endpoints', () => {
  expect(connectionAddress('http://100.80.10.2:4444/')).toBe('http://100.80.10.2:4444');
  expect(connectionAddress('https://relay.example/rooms/'+'a'.repeat(48))).toContain('/rooms/');
  for(const address of ['http://example.invalid','https://user:password@example.invalid','https://example.invalid/path','https://example.invalid?secret=x']) {
    expect(()=>connectionAddress(address)).toThrow();
  }
});

it('keeps a trusted native passcode activity locked until its fresh result returns',async()=>{
  let now=100;const state=new SessionLock(()=>{},()=>now);
  let finish!: (value:boolean)=>void;
  const result=state.unlock(()=>new Promise(resolve=>{finish=resolve;}),true);
  state.background('background');expect(state.locked).toBe(true);
  now+=3000;state.background('active');finish(true);
  expect(await result).toBe(true);
  state.background('background');expect(state.locked).toBe(true);
});
it('refuses a late system result and a successful result while still backgrounded',async()=>{
  let now=100;const state=new SessionLock(()=>{},()=>now);
  let finish!: (value:boolean)=>void;
  const stale=state.unlock(()=>new Promise(resolve=>{finish=resolve;}),true);
  now+=60001;finish(true);expect(await stale).toBe(false);
  const hidden=state.unlock(async()=>{state.background('background');return true;},true);
  expect(await hidden).toBe(false);expect(state.locked).toBe(true);
});

import {credentialIdentity} from '../../src/protocol/connection.ts';
it('keeps one queue owner while its route changes, and rotates it for a new grant',()=>{
  const credentials={baseUrl:'http://127.0.0.1:1000',deviceId:'phone:test',grantId:'one',grantSecret:'test',deviceSeedHex:'00',desktopFingerprint:'fp',pairedAt:'now'};
  expect(credentialIdentity(credentials)).toBe(credentialIdentity({...credentials,baseUrl:'https://relay.example/rooms/'+'a'.repeat(48)}));
  expect(credentialIdentity(credentials)).not.toBe(credentialIdentity({...credentials,grantId:'two'}));
});

test('a shorter selected idle time locks at its exact boundary',async()=>{
  let now=0;const lock=new SessionLock(()=>{},()=>now,60_000);
  expect(await lock.unlock(async()=>true)).toBe(true);now=59_999;lock.tick();expect(lock.locked).toBe(false);
  now=60_000;lock.tick();expect(lock.locked).toBe(true);
});
