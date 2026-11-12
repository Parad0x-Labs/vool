import {AWAY_PROBE_TIMEOUT_MS, AWAY_REQUEST_TIMEOUT_MS, MobileCompanionClient, ROUTE_PROBE_TIMEOUT_MS, CompanionError, defaultFetch, reachForPairing, setPrivateRoute, type DeviceCredentials, type FetchLike} from "../../src/protocol/client";

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

describe("pairing from anywhere", () => {
  const HOME = "http://192.168.1.20:5000";
  const challenge = {ok: true, challenge: "c", pairing_id: "p"};
  afterEach(() => setPrivateRoute(null));

  it("pairs over the home network when it answers, without waking the private route", async () => {
    const ready = jest.fn(async () => {});
    setPrivateRoute({fetch: async () => {throw new Error("unused");}, ready, stop() {}});
    const urls: string[] = [];
    const reached = await reachForPairing(HOME, HIDDEN, "p", async url => {urls.push(url); return {status: 200, json: async () => challenge};});
    expect(reached.base).toBe(HOME);
    expect(urls).toEqual([HOME + "/api/mobile/pairing/challenge?pairing_id=p"]);
    expect(ready).not.toHaveBeenCalled();
  });

  it("uses the away address from the pairing code when home is out of reach", async () => {
    const order: string[] = [];
    setPrivateRoute({fetch: async () => {throw new Error("unused");}, ready: async () => {order.push("route ready");}, stop() {}});
    const reached = await reachForPairing(HOME, HIDDEN, "p", async (url, init) => {
      order.push(url.startsWith(HIDDEN) ? "away " + init.timeoutMs : "home " + init.timeoutMs);
      if (!url.startsWith(HIDDEN)) throw new TypeError("Network request failed");
      return {status: 200, json: async () => challenge};
    });
    expect(reached.base).toBe(HIDDEN);
    expect(order).toEqual(["home " + ROUTE_PROBE_TIMEOUT_MS, "route ready", "away " + AWAY_PROBE_TIMEOUT_MS]);
  });

  it("does not go round a refusal from the computer", async () => {
    const ready = jest.fn(async () => {});
    setPrivateRoute({fetch: async () => {throw new Error("unused");}, ready, stop() {}});
    await expect(reachForPairing(HOME, HIDDEN, "p", async () => ({status: 410, json: async () => ({ok: false, code: "pairing_expired"})})))
      .rejects.toBeInstanceOf(CompanionError);
    expect(ready).not.toHaveBeenCalled();
  });

  it("fails plainly without an away address", async () => {
    await expect(reachForPairing(HOME, "", "p", async () => {throw new TypeError("Network request failed");})).rejects.toThrow("Network request failed");
  });
});
