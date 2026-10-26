// Exercise the actual Expo/Metro consumers of the patched transitive packages.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdtemp, mkdir, writeFile, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { createServer } from "node:http";
import { once } from "node:events";
import { Readable, Writable } from "node:stream";
import { pipeline } from "node:stream/promises";

const require = createRequire(import.meta.url);
const expoRequire = createRequire(require.resolve("@expo/cli/package.json"));
const metroRequire = createRequire(require.resolve("metro/package.json"));
const cliRoot = path.dirname(require.resolve("@expo/cli/package.json"));

async function archiveFixture() {
  const root = await mkdtemp(path.join(os.tmpdir(), "vool-toolchain-"));
  await mkdir(path.join(root, "package"));
  await writeFile(path.join(root, "package", "payload.txt"), "preserved");
  const archive = path.join(root, "fixture.tgz");
  await expoRequire("tar").create({ file: archive, cwd: root, gzip: true }, ["package"]);
  const output = path.join(root, "output");
  await mkdir(output);
  return { root, archive, output };
}

test("Metro reads real PNG dimensions through patched image-size", async () => {
  const { getAssetData } = metroRequire("./src/Assets");
  const asset = path.resolve("assets/icon.png");
  const data = await getAssetData(asset, "assets/icon.png", [], null, "/assets");
  assert.ok(data.width > 0 && data.height > 0);
  assert.equal(data.type, "png");
});

test("Expo npm-template extraction uses patched tar through its real import", async () => {
  const { root, archive, output } = await archiveFixture();
  try {
    const { extractLocalNpmTarballAsync } = require(path.join(cliRoot, "build/src/utils/npm.js"));
    await extractLocalNpmTarballAsync(archive, { cwd: output, name: "fixture" });
    assert.equal(await readFile(path.join(output, "payload.txt"), "utf8"), "preserved");
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("Expo Windows extraction fallback remains functional", async () => {
  const { root, archive, output } = await archiveFixture();
  const descriptor = Object.getOwnPropertyDescriptor(process, "platform");
  try {
    const { extractAsync } = require(path.join(cliRoot, "build/src/utils/tar.js"));
    Object.defineProperty(process, "platform", { value: "win32" });
    await extractAsync(archive, output);
    assert.equal(await readFile(path.join(output, "package/payload.txt"), "utf8"), "preserved");
  } finally {
    Object.defineProperty(process, "platform", descriptor);
    await rm(root, { recursive: true, force: true });
  }
});

test("Expo plist and CSS consumers preserve their native configuration data", () => {
  const plist = require("@expo/plist").default;
  const input = { CFBundleName: "VOOL & Companion", Enabled: true, Values: ["one", "two"] };
  assert.deepEqual(plist.parse(plist.build(input)), input);
  const cssRequire = createRequire(require.resolve("@expo/metro-config/package.json"));
  const postcss = cssRequire("postcss");
  const css = "a { color: red; --label: \"VOOL\"; }";
  assert.equal(postcss.parse(css).toString(), css);
});

test("Existing CommonJS consumers retain UUID v1 and v4 APIs", () => {
  for (const name of ["@expo/bunyan", "@expo/rudder-sdk-node", "xcode"]) {
    const dependency = createRequire(require.resolve(name + "/package.json"))("uuid");
    assert.equal(dependency.version(dependency.v1()), 1);
    assert.equal(dependency.version(dependency.v4()), 4);
  }
});

test("Expo's patched fetch fallback reads responses and rejects excessive content codings", async () => {
  const fetchModule = path.join(cliRoot, "build/src/utils/fetch.js");
  const saved = Object.fromEntries(["fetch", "Headers"].map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  let fetch;
  try {
    delete globalThis.fetch;
    delete globalThis.Headers;
    delete require.cache[require.resolve(fetchModule)];
    ({ fetch } = require(fetchModule));
    assert.equal(fetch, expoRequire("undici").fetch);
    assert.equal(expoRequire("undici/package.json").version, "6.29.1");
  } finally {
    for (const [name, descriptor] of Object.entries(saved)) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
    }
    delete require.cache[require.resolve(fetchModule)];
  }
  const server = createServer((req, res) => {
    if (req.url === "/hostile") {
      res.writeHead(200, { "Content-Encoding": "gzip, gzip, gzip, gzip, gzip, gzip", "Content-Length": "10" });
      res.end();
    } else {
      res.end("preserved");
    }
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  try {
    const url = `http://127.0.0.1:${server.address().port}`;
    assert.equal(await (await fetch(url)).text(), "preserved");
    await assert.rejects(fetch(`${url}/hostile`), TypeError);
    // A rejected fetch must not leave an orphaned stream error on the next turn.
    await new Promise(resolve => setImmediate(resolve));
  } finally {
    await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  }
});

test("The installed EventSource parser preserves events and bounds tiny retained fields and chunks", async () => {
  const { EventSourceStream } = expoRequire("undici/lib/web/eventsource/eventsource-stream.js");
  const parse = async chunks => {
    const events = [];
    await pipeline(
      Readable.from(chunks),
      new EventSourceStream({ maxEventSize: 1024 * 1024 }),
      new Writable({ objectMode: true, write(event, encoding, done) { events.push(event); done(); } }),
    );
    return events;
  };
  const events = await parse([Buffer.from("data: preserved\n\n")]);
  assert.equal(events.length, 1);
  assert.equal(events[0].options.data, "preserved");
  // Both inputs are far below the byte limit; the retained-item bounds must refuse them.
  await assert.rejects(parse(Array(9000).fill(Buffer.from("data:x\n"))), /EventSource message size exceeded/);
  await assert.rejects(parse(Array(9000).fill(Buffer.from("x"))), /EventSource message size exceeded/);
});
