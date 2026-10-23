import {NotificationPoller} from '../../src/protocol/notifications';
import {CompanionError, type MobileCompanionClient} from '../../src/protocol/client';

test('a stopped poller cannot present a late response after locking or changing devices',async()=>{
 let resolve!:(value:unknown)=>void;
 const client={pollNotifications:()=>new Promise(r=>{resolve=r;})} as unknown as MobileCompanionClient;
 const onEvent=jest.fn(),onBadge=jest.fn(),notifyEvent=jest.fn();
 const poller=new NotificationPoller(client,{onEvent},async()=>{},1,{onBadge,osBridge:{notify:async()=>{},notifyEvent}});
 const pending=poller.runOnce();for(let n=0;n<3;n++)await Promise.resolve();poller.stop();
 resolve({events:[{seq:1,event_type:'payment_done',target:{screen:'wallet'}}],nextAfter:1,badge:1});
 expect(await pending).toBe(0);expect(onEvent).not.toHaveBeenCalled();expect(onBadge).not.toHaveBeenCalled();expect(notifyEvent).not.toHaveBeenCalled();
});
test('late revocation from an old poller cannot revoke a new client',async()=>{
 let reject!:(error:unknown)=>void;const onRevoked=jest.fn();
 const client={pollNotifications:()=>new Promise((_,r)=>{reject=r;})} as unknown as MobileCompanionClient;
 const poller=new NotificationPoller(client,{onEvent:async()=>{}},async()=>{},1,{onRevoked});
 const pending=poller.run();for(let n=0;n<3;n++)await Promise.resolve();poller.stop();
 reject(new CompanionError(401,'device_revoked','unpaired'));await pending;expect(onRevoked).not.toHaveBeenCalled();
});
