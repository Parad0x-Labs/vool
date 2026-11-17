import {DEFAULT_CAMERA_PREFS, megapixels, parseCameraPrefs} from "../../src/capture/cameraPrefs.ts";

jest.mock("../../src/capture/native.ts", () => ({capture: null, PHOTO_FILTERS: ["none", "warm", "cool", "vivid", "fade", "mono", "noir"]}));

describe("camera settings", () => {
  it("falls back to the defaults for nothing saved or damaged text", () => {
    expect(parseCameraPrefs("")).toEqual(DEFAULT_CAMERA_PREFS);
    expect(parseCameraPrefs("{not json")).toEqual(DEFAULT_CAMERA_PREFS);
    expect(parseCameraPrefs("null")).toEqual(DEFAULT_CAMERA_PREFS);
  });

  it("keeps known choices and drops unknown ones", () => {
    const prefs = parseCameraPrefs(JSON.stringify({flash: "auto", timer: 10, grid: 7, aspect: "16:9", photoSize: "4000x3000", filter: "noir", steady: false, mirror: "yes"}));
    expect(prefs.flash).toBe("auto");
    expect(prefs.timer).toBe(10);
    expect(prefs.grid).toBe(0);
    expect(prefs.aspect).toBe("16:9");
    expect(prefs.photoSize).toBe("4000x3000");
    expect(prefs.filter).toBe("noir");
    expect(prefs.steady).toBe(false);
    expect(prefs.mirror).toBe(false);
  });

  it("refuses a photo size that is not WIDTHxHEIGHT", () => {
    expect(parseCameraPrefs(JSON.stringify({photoSize: "../../x"})).photoSize).toBe("max");
  });

  it("names sizes in megapixels", () => {
    expect(megapixels("3264x2448")).toBe("8 MP");
    expect(megapixels("4000x3000")).toBe("12 MP");
    expect(megapixels("1920x1080")).toBe("2.1 MP");
    expect(megapixels("bad")).toBe("");
  });
});
