package systems.vool.capture

import android.app.StatusBarManager
import android.content.ComponentName
import android.content.Context
import android.content.pm.PackageManager
import android.graphics.drawable.Icon
import android.os.Build
import androidx.core.content.ContextCompat
import expo.modules.kotlin.Promise
import expo.modules.kotlin.exception.Exceptions
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition

class VoolCaptureModule : Module() {
  private val context: Context
    get() = appContext.reactContext ?: throw Exceptions.ReactContextLost()

  private val launcher: ComponentName
    get() = ComponentName(context, CameraLauncherActivity::class.java)

  /**
   * Calls the optional compression package (systems.vool.capture.compress) by name, so this module
   * builds and runs the same with or without it. Without it, every call answers null.
   */
  private fun compression(name: String, vararg args: Any): Any? {
    val owner = when (name) {
      "install" -> "systems.vool.capture.compress.CaptureCompression"
      else -> "systems.vool.capture.compress.CompressionBridge"
    }
    val type = try { Class.forName(owner) } catch (_: ClassNotFoundException) { return null }
    val target = try { type.getField("INSTANCE").get(null) } catch (_: Exception) { null }
    val method = type.methods.firstOrNull { it.name == name && it.parameterTypes.size == args.size } ?: return null
    return method.invoke(target, *args)
  }

  override fun definition() = ModuleDefinition {
    Name("VoolCapture")

    Constants("pieceBytes" to CaptureStore.PIECE)

    Events("onStored")

    OnCreate {
      val ctx = appContext.reactContext ?: return@OnCreate
      CaptureStore.onStored = { note -> sendEvent("onStored", note) }
      // Optional compression lives in its own package; when it is present it installs CaptureStore.beforeStore.
      try { compression("install", ctx) } catch (_: Exception) {}
      CaptureStore.resume(ctx)
    }

    OnDestroy { CaptureStore.onStored = null }

    Function("savingCount") { CaptureStore.savingCount }

    /** On while captures are being sent: keeps sending when the phone locks, with a quiet notice. */
    Function("keepSending") { on: Boolean, text: String ->
      if (on) CaptureSendService.start(context, text) else { CaptureSendService.stop(context); false }
    }

    Function("getCompressionSettings") { compression("getJson", context) as? String ?: "" }

    Function("setCompressionSettings") { json: String -> compression("setJson", context, json) as? String ?: "" }

    Function("getCompressionCapabilities") { compression("capabilitiesJson") as? String ?: "" }

    AsyncFunction("list") { CaptureStore.list(context).map { it.toMap() } }

    AsyncFunction("readFrom") { id: String, offset: Double -> CaptureStore.readFrom(context, id, offset.toLong()) }

    AsyncFunction("remove") { id: String -> CaptureStore.remove(context, id) }

    AsyncFunction("startSound") { SoundRecorder.start(context) }

    AsyncFunction("stopSound") {
      SoundRecorder.stop(context)
      mapOf("saving" to true, "kind" to "sound")
    }

    Function("soundRecording") { SoundRecorder.recording }

    Function("homeIconOn") {
      context.packageManager.getComponentEnabledSetting(launcher) == PackageManager.COMPONENT_ENABLED_STATE_ENABLED
    }

    AsyncFunction("setHomeIcon") { on: Boolean ->
      context.packageManager.setComponentEnabledSetting(
        launcher,
        if (on) PackageManager.COMPONENT_ENABLED_STATE_ENABLED else PackageManager.COMPONENT_ENABLED_STATE_DISABLED,
        PackageManager.DONT_KILL_APP,
      )
    }

    Function("canAddTile") { Build.VERSION.SDK_INT >= 33 }

    AsyncFunction("addTile") { promise: Promise ->
      if (Build.VERSION.SDK_INT < 33) {
        promise.resolve("unsupported")
        return@AsyncFunction
      }
      val bar = context.getSystemService(StatusBarManager::class.java)
      bar.requestAddTileService(
        ComponentName(context, CaptureTileService::class.java),
        "VOOL Camera",
        Icon.createWithResource(context, R.drawable.ic_vool_capture_tile),
        ContextCompat.getMainExecutor(context),
      ) { result ->
        promise.resolve(
          when (result) {
            StatusBarManager.TILE_ADD_REQUEST_RESULT_TILE_ADDED -> "added"
            StatusBarManager.TILE_ADD_REQUEST_RESULT_TILE_ALREADY_ADDED -> "already"
            else -> "not_added"
          }
        )
      }
    }

    View(VoolCameraView::class) {
      Events("onModes", "onCaptured", "onCameraError")
      Prop("facing") { view: VoolCameraView, value: String -> view.setFacing(value) }
      Prop("mode") { view: VoolCameraView, value: String -> view.setMode(value) }
      Prop("video") { view: VoolCameraView, value: Boolean -> view.setVideo(value) }
      AsyncFunction("takePhoto") { view: VoolCameraView, promise: Promise -> view.takePhoto(promise) }
      AsyncFunction("startVideo") { view: VoolCameraView, promise: Promise -> view.startVideo(promise) }
      AsyncFunction("stopVideo") { view: VoolCameraView, promise: Promise -> view.stopVideo(promise) }
    }
  }
}
