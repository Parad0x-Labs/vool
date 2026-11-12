// Jest — MobileCompanionClient app logic: the dual-proof request assembly and
// the phone-initiated unpair action, verified against an injected fetch. The
// HMAC is re-checked with node:crypto over the same canonical bytes the
// desktop's Python authority verifies (the load-bearing cross-check of this
// lane; the node --test protocol suite pins the cryptography itself).
import { createHmac } from "node:crypto";

import { canonicalBytes } from "../../src/protocol/canonical.ts";
import {
  CompanionError,
  MobileCompanionClient,
  type DeviceCredentials,
  type FetchLike,
} from "../../src/protocol/client.ts";
import { deviceIdFor, generateDeviceKeypair } from "../../src/protocol/ed25519.ts";

function testCredentials(): DeviceCredentials {
  const kp = generateDeviceKeypair();
  return {
    baseUrl: "http://192.168.1.10:11435",
    deviceId: deviceIdFor(kp.publicKeyHex),
    grantId: "grant_abcdef123456",
    grantSecret: "bearer-secret-0123456789abcdef",
    deviceSeedHex: kp.seedHex,
    desktopFingerprint: "f".repeat(64),
    pairedAt: "2026-10-18T00:00:00.000Z",
  };
}

interface Captured {
  url: string;
  init: { method: string; headers: Record<string, string>; body?: string };
}

function captureFetch(status: number, payload: unknown): { fetchImpl: FetchLike; calls: Captured[] } {
  const calls: Captured[] = [];
  const fetchImpl: FetchLike = async (url, init) => {
    calls.push({ url, init });
    return {
      status,
      json: async () => payload,
    };
  };
  return { fetchImpl, calls };
}

describe("MobileCompanionClient devices.unpair", () => {
  it("posts one dual-proven devices.unpair request and returns the revoked row", async () => {
    const creds = testCredentials();
    const { fetchImpl, calls } = captureFetch(200, {
      ok: true,
      action: "devices.unpair",
      device_id: creds.deviceId,
      result: { device_id: creds.deviceId, status: "revoked", revoked_at: "2026-10-18T06:00:00Z" },
    });
    const client = new MobileCompanionClient(creds, { fetchImpl });

    const result = await client.unpair();

    expect(result).toEqual({
      device_id: creds.deviceId,
      status: "revoked",
      revoked_at: "2026-10-18T06:00:00Z",
    });
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe(creds.baseUrl + "/api/mobile/companion");
    expect(calls[0].init.method).toBe("POST");
    expect(calls[0].init.headers["content-type"]).toBe("application/json");

    const body = JSON.parse(calls[0].init.body ?? "{}");
    expect(body.action).toBe("devices.unpair");
    expect(body.params).toEqual({}); // no target naming: only the caller can be unpaired
    expect(body.device_id).toBe(creds.deviceId);
    expect(body.grant_id).toBe(creds.grantId);
    expect(typeof body.signature).toBe("string");
    expect(body.signature).toHaveLength(64); // grant HMAC (hex sha256)
    expect(typeof body.device_signature).toBe("string");
    expect(body.device_signature).toHaveLength(128); // Ed25519 signature (hex)
  });

  it("signs the grant HMAC over the canonical bytes the desktop verifies", async () => {
    const creds = testCredentials();
    const { fetchImpl, calls } = captureFetch(200, {
      ok: true,
      action: "devices.unpair",
      device_id: creds.deviceId,
      result: { device_id: creds.deviceId, status: "revoked" },
    });
    const client = new MobileCompanionClient(creds, { fetchImpl });
    await client.unpair();

    const body = JSON.parse(calls[0].init.body ?? "{}");
    const payload = {
      grant_id: creds.grantId,
      verb: "vool.action",
      params: { action: "devices.unpair" },
      nonce: body.nonce,
      timestamp: body.timestamp,
    };
    const expected = createHmac("sha256", Buffer.from(creds.grantSecret, "utf8"))
      .update(canonicalBytes(payload))
      .digest("hex");
    expect(body.signature).toBe(expected);
  });

  it("surfaces device_revoked as a typed CompanionError after a desktop-side revoke", async () => {
    const creds = testCredentials();
    const { fetchImpl } = captureFetch(401, {
      ok: false,
      error: "this device's authority was revoked on the desktop",
      code: "device_revoked",
    });
    const client = new MobileCompanionClient(creds, { fetchImpl });

    await expect(client.unpair()).rejects.toMatchObject({
      status: 401,
      code: "device_revoked",
    });
    // The typed error is what the UI's unpair guard branches on.
    const err = await client.unpair().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(CompanionError);
  });
});
