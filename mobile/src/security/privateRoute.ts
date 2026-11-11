import {requireOptionalNativeModule} from 'expo-modules-core';
import {setPrivateRoute, type FetchLike} from '../protocol/client.ts';

type NativeRoute = {
  request(url: string, method: string, headers: Record<string, string>, body: string | null, timeoutMs: number): Promise<{status: number; body: string}>;
  ready(timeoutMs: number): Promise<boolean>;
  stop(): Promise<void>;
};

/** Build the private route from the native part. Kept apart so tests can hand in a stand-in. */
export function privateRouteFrom(native: NativeRoute) {
  const fetchHidden: FetchLike = async (url, init) => {
    const reply = await native.request(url, init.method, init.headers, init.body ?? null, init.timeoutMs ?? 60000);
    const body: unknown = JSON.parse(String(reply.body || "null"));
    return {status: Number(reply.status), json: async () => body};
  };
  return {fetch: fetchHidden, ready: async (timeoutMs: number) => {await native.ready(timeoutMs);}, stop: () => {void native.stop().catch(() => {});}};
}

/** Connect the app's built-in private route (Tor inside the app), where this phone has it.
 *  Nothing starts here: the route only wakes when an away address is actually asked for. */
export function installPrivateRoute(): boolean {
  const native = requireOptionalNativeModule<NativeRoute>('VoolPrivateRoute');
  if (!native) return false;
  setPrivateRoute(privateRouteFrom(native));
  return true;
}
