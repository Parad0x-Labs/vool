package systems.vool.approval

import android.os.Build
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyInfo
import android.security.keystore.KeyProperties
import android.security.keystore.StrongBoxUnavailableException
import android.util.Base64
import androidx.biometric.BiometricManager
import androidx.biometric.BiometricPrompt
import androidx.core.content.ContextCompat
import androidx.fragment.app.FragmentActivity
import expo.modules.kotlin.Promise
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition
import kotlinx.coroutines.launch
import java.security.KeyFactory
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.PrivateKey
import java.security.Signature
import java.security.spec.ECGenParameterSpec

class VoolApprovalKeyModule : Module() {
  private fun alias(value: String): String {
    require(Regex("^[a-f0-9]{64}$").matches(value))
    return "systems.vool.approval.$value"
  }
  private fun store() = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
  override fun definition() = ModuleDefinition {
    Name("VoolApprovalKey")
    AsyncFunction("setPrivateSession") { enabled: Boolean ->
      // expo-screen-capture owns FLAG_SECURE for this Activity.
      Unit
    }
    AsyncFunction("create") { value: String, challenge: String ->
      val name = alias(value)
      val ks = store()
      require(!ks.containsAlias(name)) { "A fresh pairing identity is required." }
      val nonce = Base64.decode(challenge, Base64.NO_WRAP)
      require(nonce.size in 16..128)
      fun generate(strong: Boolean) {
        val spec = KeyGenParameterSpec.Builder(name, KeyProperties.PURPOSE_SIGN)
          .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
          .setDigests(KeyProperties.DIGEST_SHA256)
          .setUserAuthenticationRequired(true)
          .setInvalidatedByBiometricEnrollment(true)
          .setAttestationChallenge(nonce)
        if (Build.VERSION.SDK_INT >= 30) spec.setUserAuthenticationParameters(0, KeyProperties.AUTH_BIOMETRIC_STRONG)
        else spec.setUserAuthenticationValidityDurationSeconds(-1)
        if (Build.VERSION.SDK_INT >= 28) spec.setIsStrongBoxBacked(strong)
        KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, "AndroidKeyStore").apply {
          initialize(spec.build()); generateKeyPair()
        }
      }
      try { generate(Build.VERSION.SDK_INT >= 28) }
      catch (exception: StrongBoxUnavailableException) { ks.deleteEntry(name); generate(false) }
      val privateKey = ks.getKey(name, null) as PrivateKey
      val info = KeyFactory.getInstance(privateKey.algorithm, "AndroidKeyStore").getKeySpec(privateKey, KeyInfo::class.java)
      val hardwareBacked = if (Build.VERSION.SDK_INT >= 31) {
        info.securityLevel == KeyProperties.SECURITY_LEVEL_TRUSTED_ENVIRONMENT ||
          info.securityLevel == KeyProperties.SECURITY_LEVEL_STRONGBOX
      } else {
        @Suppress("DEPRECATION")
        info.isInsideSecureHardware
      }
      if (!hardwareBacked || !info.isUserAuthenticationRequired) {
        ks.deleteEntry(name); throw IllegalStateException("A protected approval key is unavailable.")
      }
      mapOf("public_key" to Base64.encodeToString(ks.getCertificate(name).publicKey.encoded, Base64.NO_WRAP),
        "encoding" to "spki", "platform" to "android", "build_proven" to false,
        "chain" to ks.getCertificateChain(name).map { Base64.encodeToString(it.encoded, Base64.NO_WRAP) })
    }
    AsyncFunction("sign") { value: String, payload: String, reason: String, promise: Promise ->
      appContext.mainQueue.launch {
        try {
          val activity = appContext.throwingActivity as FragmentActivity
          val bytes = Base64.decode(payload, Base64.NO_WRAP)
          require(bytes.size <= 16384)
          val signature = Signature.getInstance("SHA256withECDSA").apply {
            initSign(store().getKey(alias(value), null) as PrivateKey); update(bytes)
          }
          val prompt = BiometricPrompt(activity, ContextCompat.getMainExecutor(activity),
            object : BiometricPrompt.AuthenticationCallback() {
              override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) {
                try {
                  val signed = result.cryptoObject?.signature ?: throw IllegalStateException("Approval key missing.")
                  promise.resolve(Base64.encodeToString(signed.sign(), Base64.NO_WRAP))
                } catch (exception: Exception) { promise.reject("approval_key_unavailable", "Pair again to approve.", exception) }
              }
              override fun onAuthenticationError(code: Int, message: CharSequence) {
                promise.reject("approval_cancelled", "Nothing was approved.", null)
              }
            })
          prompt.authenticate(BiometricPrompt.PromptInfo.Builder().setTitle(reason)
            .setAllowedAuthenticators(BiometricManager.Authenticators.BIOMETRIC_STRONG)
            .setNegativeButtonText("Cancel").build(), BiometricPrompt.CryptoObject(signature))
        } catch (exception: Exception) { promise.reject("approval_key_unavailable", "Pair again to approve.", exception) }
      }
    }
    AsyncFunction("delete") { value: String -> store().deleteEntry(alias(value)) }
  }
}
