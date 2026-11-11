import {MobileCompanionClient, CompanionError, type DeviceCredentials, type FetchLike} from "../../src/protocol/client";

const creds = (extra: Partial<DeviceCredentials> = {}): DeviceCredentials => ({
  baseUrl: "http://192.168.1.20:5000", deviceId: "phone:1", grantId: "g", grantSecret: "s",
  deviceSeedHex: "11".repeat(32), desktopFingerprint: "ab".repeat(32), pairedAt: "2026-11-11T00:00:00Z", ...extra,
} as DeviceCredentials);
const ok = {status: 200, json: async () => ({ok: true, result: {device_id: "phone:1"}})};
const AWAY = "https://relay.example/rooms/" + "a".repeat(48);

function client(reachable: (url: string) => boolean, extra: Partial<DeviceCredentials> = {}) {
  const calls: {url: string; nonce: string}[] = [];
  const fetchImpl: FetchLike = async (url, init) => {
    calls.push({url, nonce: JSON.parse(init.body || "{}").nonce});
    if (!reachable(url)) throw new TypeError("Network request failed");
    return ok;
  };
  return {client: new MobileCompanionClient(creds(extra), {fetchImpl}), calls};
}

describe("home and away routes", () => {
  it("stays on the home address when it answers", async () => {
    const {client: c, calls} = client(() => true, {awayUrl: AWAY});
    await c.devicesMe();
    expect(c.route).toBe("home");
    expect(calls.every(call => call.url.startsWith("http://192.168.1.20:5000"))).toBe(true);
  });

  it("moves to the away address when home is silent, and never reuses a one-time number", async () => {
    const {client: c, calls} = client(url => url.startsWith("https://relay.example"), {awayUrl: AWAY});
    await expect(c.devicesMe()).rejects.toBeInstanceOf(TypeError);
    expect(await c.findRoute()).toBe("away");
    await c.devicesMe();
    expect(c.route).toBe("away");
    expect(calls[calls.length - 1].url).toBe(AWAY + "/api/mobile/companion");
    expect(new Set(calls.map(call => call.nonce)).size).toBe(calls.length);
  });

  it("goes back home when home answers again", async () => {
    let home = false;
    const {client: c} = client(url => home || url.startsWith("https://relay.example"), {awayUrl: AWAY});
    expect(await c.findRoute()).toBe("away");
    home = true;
    expect(await c.findRoute()).toBe("home");
  });

  it("has nothing to switch to without an away address", async () => {
    const {client: c} = client(() => false);
    expect(await c.findRoute()).toBeNull();
    await expect(c.devicesMe()).rejects.toBeInstanceOf(TypeError);
    expect(c.route).toBe("home");
  });

  it("a refusal from the computer does not count as a lost link", async () => {
    const fetchImpl: FetchLike = async () => ({status: 403, json: async () => ({ok: false, code: "phone_access_off", error: "off"})});
    const c = new MobileCompanionClient(creds({awayUrl: AWAY}), {fetchImpl});
    await expect(c.devicesMe()).rejects.toBeInstanceOf(CompanionError);
    expect(c.route).toBe("home");
  });
});
