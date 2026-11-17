package systems.vool.capture

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.view.ViewGroup
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.Preview
import androidx.camera.core.resolutionselector.ResolutionSelector
import androidx.camera.core.resolutionselector.ResolutionStrategy
import androidx.camera.extensions.ExtensionMode
import androidx.camera.extensions.ExtensionsManager
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.video.FallbackStrategy
import androidx.camera.video.FileOutputOptions
import androidx.camera.video.Quality
import androidx.camera.video.QualitySelector
import androidx.camera.video.Recorder
import androidx.camera.video.Recording
import androidx.camera.video.VideoCapture
import androidx.camera.video.VideoRecordEvent
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleOwner
import expo.modules.kotlin.AppContext
import expo.modules.kotlin.Promise
import expo.modules.kotlin.viewevent.EventDispatcher
import expo.modules.kotlin.views.ExpoView
import java.util.concurrent.Executors

/**
 * The private camera's viewfinder. Photos use the phone's own camera extensions (night, HDR,
 * portrait, retouch) when the phone offers them, at the highest resolution it has; videos record at
 * the highest quality it has. Every capture is written inside VOOL and locked into CaptureStore at
 * once; nothing reaches the gallery.
 */
class VoolCameraView(context: Context, appContext: AppContext) : ExpoView(context, appContext) {
  override val shouldUseAndroidLayout = true

  private val onModes by EventDispatcher()
  private val onCaptured by EventDispatcher()
  private val onCameraError by EventDispatcher()

  private val preview = PreviewView(context).apply {
    implementationMode = PreviewView.ImplementationMode.COMPATIBLE
    scaleType = PreviewView.ScaleType.FILL_CENTER
  }
  private val worker = Executors.newSingleThreadExecutor()
  private var provider: ProcessCameraProvider? = null
  private var extensions: ExtensionsManager? = null
  private var imageCapture: ImageCapture? = null
  private var videoCapture: VideoCapture<Recorder>? = null
  private var recording: Recording? = null
  private var stopPromise: Promise? = null

  private var facing = "back"
  private var mode = "standard"
  private var video = false

  init {
    addView(preview, ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
  }

  fun setFacing(value: String) { if (value != facing) { facing = value; bind() } }
  fun setMode(value: String) { if (value != mode) { mode = value; bind() } }
  fun setVideo(value: Boolean) { if (value != video) { video = value; bind() } }

  override fun onAttachedToWindow() {
    super.onAttachedToWindow()
    val main = ContextCompat.getMainExecutor(context)
    val future = ProcessCameraProvider.getInstance(context)
    future.addListener({
      try {
        val ready = future.get()
        provider = ready
        val extensionFuture = ExtensionsManager.getInstanceAsync(context, ready)
        extensionFuture.addListener({
          extensions = try { extensionFuture.get() } catch (_: Exception) { null }
          bind()
        }, main)
      } catch (error: Exception) {
        onCameraError(mapOf("message" to (error.message ?: "camera")))
      }
    }, main)
  }

  override fun onDetachedFromWindow() {
    recording?.stop()
    provider?.unbindAll()
    super.onDetachedFromWindow()
  }

  private val modeMap = linkedMapOf(
    "night" to ExtensionMode.NIGHT,
    "hdr" to ExtensionMode.HDR,
    "portrait" to ExtensionMode.BOKEH,
    "retouch" to ExtensionMode.FACE_RETOUCH,
  )

  private fun bind() {
    val ready = provider ?: return
    val owner = appContext.currentActivity as? LifecycleOwner ?: return
    if (recording != null) return
    val base = if (facing == "front") CameraSelector.DEFAULT_FRONT_CAMERA else CameraSelector.DEFAULT_BACK_CAMERA
    val viewfinder = Preview.Builder().build().also { it.setSurfaceProvider(preview.surfaceProvider) }
    try {
      ready.unbindAll()
      if (video) {
        val recorder = Recorder.Builder()
          .setQualitySelector(QualitySelector.from(Quality.HIGHEST, FallbackStrategy.lowerQualityOrHigherThan(Quality.HIGHEST)))
          .build()
        val capture = VideoCapture.withOutput(recorder)
        videoCapture = capture
        imageCapture = null
        ready.bindToLifecycle(owner, base, viewfinder, capture)
        onModes(mapOf("modes" to emptyList<String>()))
      } else {
        val manager = extensions
        val available = modeMap.filter { manager?.isExtensionAvailable(base, it.value) == true }.keys.toList()
        onModes(mapOf("modes" to available))
        val chosen = modeMap[mode]
        val selector = if (manager != null && chosen != null && mode in available) manager.getExtensionEnabledCameraSelector(base, chosen) else base
        val capture = ImageCapture.Builder()
          .setCaptureMode(ImageCapture.CAPTURE_MODE_MAXIMIZE_QUALITY)
          .setResolutionSelector(ResolutionSelector.Builder().setResolutionStrategy(ResolutionStrategy.HIGHEST_AVAILABLE_STRATEGY).build())
          .setTargetRotation(display?.rotation ?: 0)
          .build()
        imageCapture = capture
        videoCapture = null
        ready.bindToLifecycle(owner, selector, viewfinder, capture)
      }
    } catch (error: Exception) {
      onCameraError(mapOf("message" to (error.message ?: "camera")))
    }
  }

  fun takePhoto(promise: Promise) {
    val capture = imageCapture ?: return promise.reject("camera_not_ready", "The camera is not ready yet.", null)
    val target = CaptureStore.scratch(context, "jpg")
    val takenAt = System.currentTimeMillis()
    capture.targetRotation = display?.rotation ?: capture.targetRotation
    capture.takePicture(ImageCapture.OutputFileOptions.Builder(target).build(), worker, object : ImageCapture.OnImageSavedCallback {
      override fun onImageSaved(output: ImageCapture.OutputFileResults) {
        try {
          CaptureStore.keep(context, target, "image/jpeg", "photo", takenAt)
          promise.resolve(mapOf("saving" to true, "kind" to "photo"))
        } catch (error: Exception) {
          target.delete()
          promise.reject("capture_not_stored", error.message ?: "The photo could not be stored.", error)
        }
      }

      override fun onError(error: ImageCaptureException) {
        target.delete()
        promise.reject("photo_failed", error.message ?: "The photo could not be taken.", error)
      }
    })
  }

  fun startVideo(promise: Promise) {
    val capture = videoCapture ?: return promise.reject("camera_not_ready", "The camera is not ready yet.", null)
    if (recording != null) return promise.resolve(true)
    val target = CaptureStore.scratch(context, "mp4")
    val takenAt = System.currentTimeMillis()
    var pending = capture.output.prepareRecording(context, FileOutputOptions.Builder(target).build())
    if (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
      pending = pending.withAudioEnabled()
    }
    var started = false
    recording = pending.start(ContextCompat.getMainExecutor(context)) { event ->
      if (event is VideoRecordEvent.Start && !started) {
        started = true
        promise.resolve(true)
      }
      if (event is VideoRecordEvent.Finalize) {
        recording = null
        val waiting = stopPromise
        stopPromise = null
        try {
          // Some endings (the app went to the background, the disk filled up) still leave a usable file.
          if (!target.isFile || target.length() == 0L) throw IllegalStateException("The video could not be recorded.")
          // Locking it away (and any compression) runs on the saver; the camera is free at once.
          CaptureStore.keep(context, target, "video/mp4", "video", takenAt)
          val note = mapOf("saving" to true, "kind" to "video")
          if (waiting != null) waiting.resolve(note) else onCaptured(note)
        } catch (error: Exception) {
          target.delete()
          if (waiting != null) waiting.reject("video_failed", error.message ?: "The video could not be stored.", error)
          else onCameraError(mapOf("message" to (error.message ?: "video")))
          if (!started) promise.reject("video_failed", error.message ?: "The video could not start.", error)
        }
      }
    }
  }

  fun stopVideo(promise: Promise) {
    val current = recording ?: return promise.reject("not_recording", "Nothing is recording.", null)
    stopPromise = promise
    current.stop()
  }
}
