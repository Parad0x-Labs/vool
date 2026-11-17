// Xcode 26's compiler rejects the compile-time format checks in fmt 9 (used by React Native 0.76), so the fmt pod
// alone is built as C++17, where fmt does not use them. Nothing else changes.
const {withPodfile} = require("expo/config-plugins");

const MARK = "fmt builds as C++17";
const BLOCK = `
    # ${MARK} (plugins/withFmtForNewXcode.cjs)
    installer.pods_project.targets.each do |target|
      next unless target.name == 'fmt'
      target.build_configurations.each { |c| c.build_settings['CLANG_CXX_LANGUAGE_STANDARD'] = 'c++17' }
    end
`;

module.exports = function withFmtForNewXcode(config) {
  return withPodfile(config, (result) => {
    const podfile = result.modResults;
    if (!podfile.contents.includes(MARK)) {
      // After React Native's own post_install step, which sets the language standard of every pod.
      const call = podfile.contents.indexOf("react_native_post_install(");
      const close = call < 0 ? -1 : podfile.contents.indexOf("\n    )\n", call);
      if (close < 0) throw new Error("withFmtForNewXcode: no react_native_post_install call in the Podfile.");
      const end = close + "\n    )\n".length;
      podfile.contents = podfile.contents.slice(0, end) + BLOCK + podfile.contents.slice(end);
    }
    return result;
  });
};
