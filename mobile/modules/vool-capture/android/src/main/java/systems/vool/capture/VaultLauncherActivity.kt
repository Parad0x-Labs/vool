package systems.vool.capture

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Bundle

/** The optional "VOOL Vault" icon: opens VOOL at the Vault, behind VOOL's own unlock (fingerprint, face or phone PIN). */
class VaultLauncherActivity : Activity() {
  override fun onCreate(savedInstanceState: Bundle?) {
    super.onCreate(savedInstanceState)
    startActivity(
      Intent(Intent.ACTION_VIEW, Uri.parse("vool-pair://vault"))
        .setPackage(packageName)
        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP),
    )
    finish()
  }
}
