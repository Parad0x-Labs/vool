// The phone app talks only to the paired computer. Two libraries bring Google parts it never
// uses: the notification library ships Google's push receivers (VOOL shows its own local
// notifications), and the QR scanner ships a usage reporter that uploads to Google. This takes
// both out of the built app; scanning itself runs on the phone and keeps working.
const {withAndroidManifest, AndroidConfig} = require("expo/config-plugins");

const REMOVED = [
  ["service", "expo.modules.notifications.service.ExpoFirebaseMessagingService"],
  ["service", "com.google.firebase.messaging.FirebaseMessagingService"],
  ["receiver", "com.google.firebase.iid.FirebaseInstanceIdReceiver"],
];
const REPORTER = "com.google.android.datatransport.runtime.backends.TransportBackendDiscovery";
const REPORTER_BACKEND = "backend:com.google.android.datatransport.cct.CctBackendFactory";

module.exports = function withoutGoogleReporting(config) {
  return withAndroidManifest(config, (result) => {
    const manifest = result.modResults;
    AndroidConfig.Manifest.ensureToolsAvailable(manifest);
    const app = AndroidConfig.Manifest.getMainApplicationOrThrow(manifest);
    for (const [kind, name] of REMOVED) {
      app[kind] = (app[kind] || []).filter((item) => item.$["android:name"] !== name);
      app[kind].push({$: {"android:name": name, "tools:node": "remove"}});
    }
    app.service = (app.service || []).filter((item) => item.$["android:name"] !== REPORTER);
    app.service.push({
      $: {"android:name": REPORTER, "android:exported": "false"},
      "meta-data": [{$: {"android:name": REPORTER_BACKEND, "tools:node": "remove"}}],
    });
    return result;
  });
};
