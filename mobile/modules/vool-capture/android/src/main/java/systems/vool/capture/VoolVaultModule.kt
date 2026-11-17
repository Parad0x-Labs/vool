package systems.vool.capture

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import androidx.core.content.pm.ShortcutInfoCompat
import androidx.core.content.pm.ShortcutManagerCompat
import androidx.core.graphics.drawable.IconCompat
import expo.modules.kotlin.exception.Exceptions
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition

/** VOOL Vault's native side: sealed in-memory-keyed streams, the player, saving or sharing a copy, and its home icon. */
class VoolVaultModule : Module() {
  private val context: Context
    get() = appContext.reactContext ?: throw Exceptions.ReactContextLost()

  private val launcher: ComponentName
    get() = ComponentName(context, VaultLauncherActivity::class.java)

  override fun definition() = ModuleDefinition {
    Name("VoolVault")

    Constants("pieceBytes" to VaultStreams.PIECE)

    Events("onWant")

    OnCreate {
      appContext.reactContext?.let { VaultStreams.sweep(it) }
      VaultStreams.onWant = { stream, index -> sendEvent("onWant", mapOf("stream" to stream, "index" to index)) }
    }

    OnDestroy { VaultStreams.onWant = null; VaultStreams.closeAll() }

    Function("open") { size: Double, mediaType: String -> VaultStreams.open(context, size.toLong(), mediaType) }
    AsyncFunction("put") { stream: String, index: Int, base64: String -> VaultStreams.put(stream, index, base64) }
    Function("held") { stream: String -> VaultStreams.held(stream) }
    Function("close") { stream: String -> VaultStreams.close(stream) }
    Function("closeAll") { VaultStreams.closeAll() }
    AsyncFunction("share") { stream: String, name: String, title: String -> VaultStreams.share(context, stream, name, title) }
    AsyncFunction("save") { stream: String, name: String -> VaultStreams.save(context, stream, name) }

    /** The person turned the Vault icon on: it shows in the app list, and the phone offers to put it on the home screen. */
    Function("homeIconOn") {
      context.packageManager.getComponentEnabledSetting(launcher) == PackageManager.COMPONENT_ENABLED_STATE_ENABLED
    }
    AsyncFunction("setHomeIcon") { on: Boolean ->
      context.packageManager.setComponentEnabledSetting(
        launcher,
        if (on) PackageManager.COMPONENT_ENABLED_STATE_ENABLED else PackageManager.COMPONENT_ENABLED_STATE_DISABLED,
        PackageManager.DONT_KILL_APP,
      )
      if (!on) return@AsyncFunction "off"
      if (!ShortcutManagerCompat.isRequestPinShortcutSupported(context)) return@AsyncFunction "listed"
      val intent = Intent(Intent.ACTION_VIEW, Uri.parse("vool-pair://vault")).setPackage(context.packageName)
      val shortcut = ShortcutInfoCompat.Builder(context, "vool-vault")
        .setShortLabel("VOOL Vault")
        .setIcon(IconCompat.createWithResource(context, R.mipmap.ic_vool_vault))
        .setIntent(intent)
        .build()
      if (ShortcutManagerCompat.requestPinShortcut(context, shortcut, null)) "asked" else "listed"
    }

    View(VaultPlayerView::class) {
      Events("onPlayerError", "onPlayerReady")
      Prop("stream") { view: VaultPlayerView, value: String -> view.setStream(value) }
    }
  }
}
