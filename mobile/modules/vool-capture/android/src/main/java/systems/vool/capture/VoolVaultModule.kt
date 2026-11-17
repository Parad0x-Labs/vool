package systems.vool.capture

import android.content.ComponentName
import android.content.Context
import android.content.pm.PackageManager
import expo.modules.kotlin.Promise
import expo.modules.kotlin.exception.Exceptions
import java.util.concurrent.Executors
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition

/** VOOL Vault's native side: sealed in-memory-keyed streams, the player, saving or sharing a copy, and its home icon. */
class VoolVaultModule : Module() {
  private val context: Context
    get() = appContext.reactContext ?: throw Exceptions.ReactContextLost()

  /**
   * Expo runs every module's async functions one after another on a single thread. A preview waits for pieces
   * still on their way, and those pieces arrive through put(): on that one thread they would wait for each other
   * (and hold up the camera's calls too). So the Vault's work runs on its own threads.
   */
  private val work = Executors.newCachedThreadPool { job -> Thread(job, "vool-vault").apply { isDaemon = true } }

  private fun off(promise: Promise, code: String, job: () -> Any?) {
    work.execute {
      try { promise.resolve(job()) } catch (error: Throwable) { promise.reject(code, error.message ?: code, error) }
    }
  }

  private val launcher: ComponentName
    get() = ComponentName(context, VaultLauncherActivity::class.java)

  override fun definition() = ModuleDefinition {
    Name("VoolVault")

    Constants("pieceBytes" to VaultStreams.PIECE)

    Events("onWant")

    OnCreate {
      MainWatch.start()
      appContext.reactContext?.let { VaultStreams.sweep(it) }
      VaultStreams.onWant = { stream, index -> sendEvent("onWant", mapOf("stream" to stream, "index" to index)) }
    }

    OnDestroy { VaultStreams.onWant = null; VaultStreams.closeAll() }

    Function("open") { size: Double, mediaType: String -> VaultStreams.open(context, size.toLong(), mediaType) }
    AsyncFunction("put") { stream: String, index: Int, base64: String, promise: Promise ->
      off(promise, "ERR_VAULT_PUT") { VaultStreams.put(stream, index, base64) }
    }
    Function("held") { stream: String -> VaultStreams.held(stream) }
    // The link's hashing and sealing, here instead of in JavaScript (same bytes, a small part of the time).
    Function("cryptoSha256Hex") { text: String -> FastCrypto.sha256Hex(text) }
    Function("cryptoHmacSha256Hex") { key: String, text: String -> FastCrypto.hmacSha256Hex(key, text) }
    Function("cryptoSeal") { text: String, nonceHex: String, keyHex: String -> FastCrypto.sealBase64(text, nonceHex, keyHex) }
    Function("cryptoOpen") { data: String, nonceHex: String, keyHex: String -> FastCrypto.openText(data, nonceHex, keyHex) ?: "" }
    Function("close") { stream: String -> VaultStreams.close(stream) }
    Function("closeAll") { VaultStreams.closeAll() }
    AsyncFunction("share") { stream: String, name: String, title: String, promise: Promise ->
      val ctx = context
      off(promise, "ERR_VAULT_SHARE") { VaultStreams.share(ctx, stream, name, title); null }
    }
    AsyncFunction("shareMany") { ids: List<String>, names: List<String>, title: String, promise: Promise ->
      val ctx = context
      off(promise, "ERR_VAULT_SHARE") { VaultStreams.shareMany(ctx, ids, names, title); null }
    }
    AsyncFunction("save") { stream: String, name: String, promise: Promise ->
      val ctx = context
      off(promise, "ERR_VAULT_SAVE") { VaultStreams.save(ctx, stream, name) }
    }

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
      // Like VOOL Camera: its own entry in the app list, which the launcher places like any app (a pinned
      // shortcut would carry the small VOOL mark on its corner).
      if (on) "listed" else "off"
    }

    /** A card preview or a screen-size picture made here from the stream, as JPEG base64 ("" when none). */
    AsyncFunction("preview") { stream: String, maxEdge: Int, kind: String, promise: Promise ->
      off(promise, "ERR_VAULT_PREVIEW") { VaultPreview.make(stream, maxEdge, kind) }
    }

    View(VaultPlayerView::class) {
      Events("onPlayerError", "onPlayerReady")
      Prop("stream") { view: VaultPlayerView, value: String -> view.setStream(value) }
    }
  }
}
