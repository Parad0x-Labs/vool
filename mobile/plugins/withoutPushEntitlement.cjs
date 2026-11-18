// VOOL shows only notifications made on the device itself; it never receives pushes from Apple's servers.
// expo-notifications adds the push entitlement anyway, which needs a push-enabled signing profile: drop it.
const {withEntitlementsPlist} = require("expo/config-plugins");

module.exports = function withoutPushEntitlement(config) {
  return withEntitlementsPlist(config, (mod) => {
    delete mod.modResults["aps-environment"];
    return mod;
  });
};
