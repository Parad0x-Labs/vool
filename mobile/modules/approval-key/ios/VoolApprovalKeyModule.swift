import ExpoModulesCore
import Security
import LocalAuthentication

// This build is deliberately unproven until compiled and exercised on devices.
// No private-key representation is returned to JavaScript.
public class VoolApprovalKeyModule: Module {
  private func tag(_ alias: String) throws -> Data {
    guard alias.range(of: "^[a-f0-9]{64}$", options: .regularExpression) != nil else {
      throw NSError(domain: "VoolApprovalKey", code: 1)
    }
    return Data(("systems.vool.approval." + alias).utf8)
  }
  private func key(_ alias: String, context: LAContext? = nil) throws -> SecKey {
    var query: [String: Any] = [kSecClass as String: kSecClassKey,
      kSecAttrApplicationTag as String: try tag(alias), kSecReturnRef as String: true,
      kSecAttrKeyType as String: kSecAttrKeyTypeECSECPrimeRandom]
    if let context = context { query[kSecUseAuthenticationContext as String] = context }
    var item: CFTypeRef?
    let status = SecItemCopyMatching(query as CFDictionary, &item)
    guard status == errSecSuccess, let item = item else {
      throw NSError(domain: "VoolApprovalKey", code: Int(status))
    }
    return item as! SecKey
  }
  public func definition() -> ModuleDefinition {
    Name("VoolApprovalKey")
    AsyncFunction("setPrivateSession") { (enabled: Bool) in
      VoolPrivacySubscriber.ready(enabled)
    }.runOnQueue(.main)
    AsyncFunction("create") { (alias: String, challenge: String) -> [String: Any] in
      let applicationTag = try self.tag(alias)
      // A fresh transport identity gets a fresh alias. Never replace a live key.
      var existing: CFTypeRef?
      let lookupContext = LAContext()
      lookupContext.interactionNotAllowed = true
      let lookup: [String: Any] = [kSecClass as String: kSecClassKey,
        kSecAttrApplicationTag as String: applicationTag, kSecReturnRef as String: true,
        kSecUseAuthenticationContext as String: lookupContext]
      guard SecItemCopyMatching(lookup as CFDictionary, &existing) == errSecItemNotFound else {
        throw NSError(domain: "VoolApprovalKey", code: 2)
      }
      var error: Unmanaged<CFError>?
      guard let access = SecAccessControlCreateWithFlags(nil, kSecAttrAccessibleWhenUnlockedThisDeviceOnly,
        [.privateKeyUsage, .biometryCurrentSet], &error) else {throw error!.takeRetainedValue()}
      let attributes: [String: Any] = [kSecAttrKeyType as String: kSecAttrKeyTypeECSECPrimeRandom,
        kSecAttrKeySizeInBits as String: 256, kSecAttrTokenID as String: kSecAttrTokenIDSecureEnclave,
        kSecPrivateKeyAttrs as String: [kSecAttrIsPermanent as String: true,
          kSecAttrApplicationTag as String: applicationTag, kSecAttrAccessControl as String: access]]
      guard let privateKey = SecKeyCreateRandomKey(attributes as CFDictionary, &error),
        let publicKey = SecKeyCopyPublicKey(privateKey),
        let bytes = SecKeyCopyExternalRepresentation(publicKey, &error) as Data? else {
        throw error?.takeRetainedValue() ?? NSError(domain: "VoolApprovalKey", code: 3)
      }
      return ["public_key": bytes.base64EncodedString(), "encoding": "x963", "platform": "ios",
        "attestation": "not_checked_in_this_build", "build_proven": false]
    }
    AsyncFunction("sign") { (alias: String, payload: String, reason: String) -> String in
      guard let bytes = Data(base64Encoded: payload), bytes.count <= 16384 else {
        throw NSError(domain: "VoolApprovalKey", code: 4)
      }
      // The key's current-set ACL itself prompts; app-session unlock is unrelated.
      let context = LAContext()
      context.localizedReason = reason
      context.touchIDAuthenticationAllowableReuseDuration = 0
      let privateKey = try self.key(alias, context: context)
      var error: Unmanaged<CFError>?
      guard let signature = SecKeyCreateSignature(privateKey, .ecdsaSignatureMessageX962SHA256,
        bytes as CFData, &error) as Data? else {
        throw error?.takeRetainedValue() ?? NSError(domain: "VoolApprovalKey", code: 5)
      }
      return signature.base64EncodedString()
    }
    AsyncFunction("delete") { (alias: String) in
      let query: [String: Any] = [kSecClass as String: kSecClassKey,
        kSecAttrApplicationTag as String: try self.tag(alias)]
      let result = SecItemDelete(query as CFDictionary)
      guard result == errSecSuccess || result == errSecItemNotFound else {
        throw NSError(domain: "VoolApprovalKey", code: Int(result))
      }
    }
  }
}
