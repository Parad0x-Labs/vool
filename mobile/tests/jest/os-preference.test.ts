import {ExpoOsNotifications} from '../../src/notifications/osNotifications.ts';
import {OsPreferenceStore} from '../../src/notifications/preference.ts';

const mockValues = new Map<string,string>();
const mockCalls: string[] = [];
let mockGranted = false;
let mockCanAsk = true;
const mockScheduled: any[] = [];
jest.mock('react-native', () => ({Platform: {OS:'android'}}));
jest.mock('expo-secure-store', () => ({
  WHEN_UNLOCKED_THIS_DEVICE_ONLY: 7,
  getItemAsync: async (key: string) => mockValues.get(key) ?? null,
  setItemAsync: async (key: string,value: string) => {mockValues.set(key,value);},
  deleteItemAsync: async (key: string) => {mockValues.delete(key);},
}));
jest.mock('expo-notifications', () => ({
  AndroidImportance:{DEFAULT:3}, AndroidNotificationVisibility:{PRIVATE:0},
  setNotificationChannelAsync: async () => {mockCalls.push('channel');},
  setNotificationHandler: async () => {mockCalls.push('handler');},
  getPermissionsAsync: async () => ({granted:mockGranted,canAskAgain:mockCanAsk}),
  requestPermissionsAsync: async () => {mockCalls.push('request');return {granted:true};},
  scheduleNotificationAsync: async (value: any) => {mockScheduled.push(value);return 'test';},
}));
beforeEach(() => {mockValues.clear();mockCalls.length=0;mockScheduled.length=0;mockGranted=false;mockCanAsk=true;});

it('defaults off, isolates device choices, and clears them on unpair',async()=>{
  const first=new OsPreferenceStore('first'),second=new OsPreferenceStore('second');
  expect(await first.load()).toBe(false);await first.save(true);
  expect(await first.load()).toBe(true);expect(await second.load()).toBe(false);
  await first.clear();expect(await first.load()).toBe(false);
});
it('restoring a choice never raises an OS permission prompt',async()=>{
  const bridge=new ExpoOsNotifications();
  expect(await bridge.restoreEnabled()).toBe(false);
  expect(mockCalls).not.toContain('request');
  mockGranted=true;expect(await bridge.restoreEnabled()).toBe(true);
  expect(mockCalls).not.toContain('request');
});
it('creates the Android channel before explicit permission and keeps coarse text by default',async()=>{
  const bridge=new ExpoOsNotifications();expect(await bridge.ensureEnabled()).toBe(true);
  expect(mockCalls.indexOf('channel')).toBeLessThan(mockCalls.indexOf('request'));
  await bridge.notifyEvent({seq:1,event_type:'payment_done',mobile_kind:'completion',message:'A payment finished.',detail:'Private amount',lock_screen_details:false});
  expect(mockScheduled[0].content.body).toBe('Payment done');
  expect(mockScheduled[0].trigger.channelId).toBe('vool');
  await bridge.notifyEvent({seq:2,event_type:'payment_done',mobile_kind:'completion',os_notification_allowed:false});
  expect(mockScheduled.length).toBe(1);bridge.disable();
  await bridge.notify('failure');expect(mockScheduled.length).toBe(1);
});
it('does not ask again when the OS has blocked notifications',async()=>{
  mockCanAsk=false;const bridge=new ExpoOsNotifications();
  expect(await bridge.ensureEnabled()).toBe(false);expect(bridge.isEnabled()).toBe(false);
  expect(mockCalls).not.toContain('request');
});
