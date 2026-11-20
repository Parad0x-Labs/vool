package systems.vool.capture

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Bundle

/** The optional "VOOL Camera" home-screen icon: opens VOOL straight at the private camera, without VOOL's unlock (the rest of VOOL stays locked). */
class CameraLauncherActivity : Activity() {
  override fun onCreate(savedInstanceState: Bundle?) {
    super.onCreate(savedInstanceState)
    android.util.Log.i("VoolCapture", "icon: VOOL Camera tapped")
    CameraLaunch.mark()
    startActivity(captureIntent(this))
    finish()
  }

  companion object {
    fun captureIntent(context: android.content.Context): Intent =
      Intent(Intent.ACTION_VIEW, Uri.parse("vool-pair://capture"))
        .setPackage(context.packageName)
        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
  }
}
