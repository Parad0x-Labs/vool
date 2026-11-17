// Whatever is typed passes through the keyboard first, and other keyboards can be allowed online.
// On iPhone and iPad, VOOL accepts only Apple's own keyboard: the app asks the system to refuse
// every other keyboard while VOOL is in front. Other apps keep the keyboard their owner chose.
const {withAppDelegate} = require("expo/config-plugins");

const MARK = "shouldAllowExtensionPointIdentifier";
const METHOD = `
// VOOL types only with Apple's own keyboard (see plugins/withAppleKeyboardOnly.cjs).
- (BOOL)application:(UIApplication *)application shouldAllowExtensionPointIdentifier:(UIApplicationExtensionPointIdentifier)extensionPointIdentifier
{
  return ![extensionPointIdentifier isEqualToString:UIApplicationKeyboardExtensionPointIdentifier];
}
`;

module.exports = function withAppleKeyboardOnly(config) {
  return withAppDelegate(config, (result) => {
    const delegate = result.modResults;
    if (delegate.language !== "objcpp" && delegate.language !== "objc") {
      throw new Error("withAppleKeyboardOnly expects the Objective-C app delegate of Expo SDK 52.");
    }
    if (!delegate.contents.includes(MARK)) {
      const end = delegate.contents.lastIndexOf("@end");
      if (end < 0) throw new Error("withAppleKeyboardOnly: no @end in the app delegate.");
      delegate.contents = delegate.contents.slice(0, end) + METHOD + "\n" + delegate.contents.slice(end);
    }
    return result;
  });
};
