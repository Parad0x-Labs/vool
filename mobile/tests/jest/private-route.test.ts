import {AWAY_PROBE_TIMEOUT_MS, AWAY_REQUEST_TIMEOUT_MS, MobileCompanionClient, ROUTE_PROBE_TIMEOUT_MS, defaultFetch, setPrivateRoute, type DeviceCredentials, type FetchLike} from "../../src/protocol/client";

const HIDDEN = "http://" + "a2".repeat(28) + ".onion";

describe("hidden addresses only use the private route", () => {
  const plain = jest.fn();
  beforeEach(() => {plain.mockReset(); (globalThis as {fetch: unknown}).fetch = plain; setPrivateRoute(null);});
  afterAll(() => setPrivateRoute(null));

  it("refuses a hidden address when the phone has no private route, without touching the network", async () => {
    await expect(defaultFetch(HIDDEN + "/api/mobile/companion", {method: "POST", headers: {}, body: "{}"})).rejects.toThrow("private route unavailable");
    expect(plain).not.toHaveBeenCalled();
  });

  it.each([
    HIDDEN, HIDDEN + "/", HIDDEN + ":80/x", HIDDEN.toUpperCase() + "/x", "https://sub." + "b".repeat(56) + ".onion/x",
    "http://short.onion/x", HIDDEN + "./x", HIDDEN + "?q=1",
  ])("never sends %s over the normal network", async url => {
    await expect(defaultFetch(url, {method: "GET", headers: {}})).rejects.toThrow("private route unavailable");
    expect(plain).not.toHaveBeenCalled();
  });

  it("sends a hidden address through the private route, with time for the route to connect", async () => {
    const seen: Array<{url: string; timeoutMs?: number}> = [];
    setPrivateRoute({fetch: async (url, init) => {seen.push({url, timeoutMs: init.timeoutMs}); return {status: 200, json: async () => ({ok: true})};}, ready: async () => {}, stop() {}});
    const reply = await defaultFetch(HIDDEN + "/api/mobile/companion", {method: "POST", headers: {}, body: "{}", timeoutMs: 5000});
    expect(reply.status).toBe(200);
    expect(seen).toEqual([{url: HIDDEN + "/api/mobile/companion", timeoutMs: AWAY_REQUEST_TIMEOUT_MS}]);
    await defaultFetch(HIDDEN + "/x", {method: "POST", headers: {}, timeoutMs: 240000});
    expect(seen[1].timeoutMs).toBe(240000);
    expect(plain).not.toHaveBeenCalled();
  });

  it("keeps ordinary addresses on the normal network", async () => {
    setPrivateRoute({fetch: async () => {throw new Error("must not be used");}, ready: async () => {}, stop() {}});
    plain.mockResolvedValue({status: 200, json: async () => ({ok: true})});
    for (const url of ["http://192.168.1.20:11500/api/mobile/companion", "https://onion.example/x", "http://example.com/a.onion"]) {
      expect((await defaultFetch(url, {method: "GET", headers: {}})).status).toBe(200);
    }
    expect(plain).toHaveBeenCalledTimes(3);
  });
});

describe("the private route rests at home", () => {
  const creds = {baseUrl: "http://192.168.1.20:5000", awayUrl: HIDDEN, deviceId: "phone:1", grantId: "g", grantSecret: "s",
    deviceSeedHex: "11".repeat(32), desktopFingerprint: "ab".repeat(32), pairedAt: "2026-11-11T00:00:00Z"} as DeviceCredentials;
  afterEach(() => setPrivateRoute(null));

  it("is told to stop when the home address answers, and is given time when it is needed", async () => {
    let home = false;
    const waits: Array<number | undefined> = [];
    const fetchImpl: FetchLike = async (url, init) => {
      waits.push(init.timeoutMs);
      if (!home && !url.startsWith(HIDDEN)) throw new TypeError("Network request failed");
      return {status: 200, json: async () => ({ok: true, result: {device_id: "phone:1"}})};
    };
    const stop = jest.fn();
    const ready = jest.fn(async () => {waits.push(-1);});
    setPrivateRoute({fetch: fetchImpl, ready, stop});
    const client = new MobileCompanionClient(creds, {fetchImpl});
    expect(await client.findRoute()).toBe("away");
    // The route is woken before the away question is made and signed.
    expect(waits).toEqual([ROUTE_PROBE_TIMEOUT_MS, -1, AWAY_PROBE_TIMEOUT_MS]);
    expect(ready).toHaveBeenCalledWith(AWAY_PROBE_TIMEOUT_MS);
    expect(stop).not.toHaveBeenCalled();
    home = true;
    expect(await client.findRoute()).toBe("home");
    expect(stop).toHaveBeenCalledTimes(1);
  });

  it("does not ask the away address at all on a phone without the private route", async () => {
    const urls: string[] = [];
    const fetchImpl: FetchLike = async url => {urls.push(url); throw new TypeError("Network request failed");};
    expect(await new MobileCompanionClient(creds, {fetchImpl}).findRoute()).toBeNull();
    expect(urls).toEqual(["http://192.168.1.20:5000/api/mobile/companion"]);
  });
});
