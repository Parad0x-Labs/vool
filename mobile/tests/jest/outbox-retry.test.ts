import {Outbox,MemoryOutboxStore,outboxChunks} from '../../src/protocol/outbox';
import {CompanionError,type MobileCompanionClient} from '../../src/protocol/client';

test('an explicit retry after permission restoration preserves the exact message identity',async()=>{
 const keys:string[]=[],texts:string[]=[];let allowed=false;
 const client={sendMessage:async(chat:string,text:string,files:string[],key:string)=>{
  keys.push(key);texts.push(text);if(!allowed)throw new CompanionError(403,'phone_right_off_or_expired','off');
  return {chat_id:chat,reply:'done',turn_id:'turn',commit:{request_id:'request'}};
 }} as unknown as MobileCompanionClient;
 const store=new MemoryOutboxStore(),box=new Outbox(store,client);
 const turn=await box.enqueue('mob-'+'1'.repeat(20),'same message',[]);await box.flush();
 expect((await store.load())[0].state).toBe('blocked');allowed=true;await box.retryBlocked(turn.key);
 expect(keys).toEqual([turn.key,turn.key]);expect(texts).toEqual(['same message','same message']);expect(await store.load()).toEqual([]);
});


test('saved multilingual messages survive each chunk’s native UTF-8 conversion exactly',()=>{
  const original=JSON.stringify({text:'a'.repeat(390)+'😀'.repeat(1000)+'עברית العربية 日本語'});
  const chunks=outboxChunks(original);
  const recovered=chunks.map(value=>new TextDecoder().decode(new TextEncoder().encode(value))).join('');
  expect(recovered).toBe(original);expect(JSON.parse(recovered)).toEqual(JSON.parse(original));
  expect(chunks.every(value=>new TextEncoder().encode(value).length<=1600)).toBe(true);
});
