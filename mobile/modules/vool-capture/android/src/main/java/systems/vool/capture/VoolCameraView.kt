package systems.vool.capture

import android.Manifest
import android.content.Context
import android.annotation.SuppressLint
import android.content.pm.PackageManager
import android.graphics.ColorMatrixColorFilter
import android.graphics.RenderEffect
import android.os.Build
import android.util.Size
import android.view.MotionEvent
import android.view.ScaleGestureDetector
import android.view.ViewGroup
import androidx.camera.core.Camera
import androidx.camera.core.CameraSelector
import androidx.camera.core.FocusMeteringAction
import androidx.camera.core.MirrorMode
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.Preview
import androidx.camera.core.resolutionselector.AspectRatioStrategy
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
  private val onZoom by EventDispatcher()
  private val onFocus by EventDispatcher()

  // FIT, not FILL: what the viewfinder shows is exactly what the photo holds.
  private val preview = PreviewView(context).apply {
    implementationMode = PreviewView.ImplementationMode.COMPATIBLE
    scaleType = PreviewView.ScaleType.FIT_CENTER
  }
  private val worker = Executors.newSingleThreadExecutor()
  private var provider: ProcessCameraProvider? = null
  private var extensions: ExtensionsManager? = null
  private var imageCapture: ImageCapture? = null
  private var videoCapture: VideoCapture<Recorder>? = null
  private var recording: Recording? = null
  private var stopPromise: Promise? = null
  private var camera: Camera? = null

  private var facing = "back"
  private var mode = "standard"
  private var video = false
  // Camera settings (the app keeps them; these follow its props)
  private var flash = "off"            // off | auto | on (in video: on = torch)
  private var aspect = "4:3"           // 4:3 | 16:9
  private var photoSize = "max"        // max | WIDTHxHEIGHT
  private var videoQuality = "highest" // highest | 2160 | 1080 | 720
  private var steady = true
  private var mirror = false
  private var filter = "none"

  init {
    addView(preview, ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
    gestures()
  }

  fun setFacing(value: String) { if (value != facing) { facing = value; bind() } }
  fun setMode(value: String) { if (value != mode) { mode = value; bind() } }
  fun setVideo(value: Boolean) { if (value != video) { video = value; bind() } }
  fun setFlash(value: String) {
    if (value == flash) return
    flash = value
    imageCapture?.flashMode = flashMode()
    if (video) camera?.cameraControl?.enableTorch(flash == "on")
  }
  fun setAspect(value: String) { if (value != aspect) { aspect = value; bind() } }
  fun setPhotoSize(value: String) { if (value != photoSize) { photoSize = value; bind() } }
  fun setVideoQuality(value: String) { if (value != videoQuality) { videoQuality = value; bind() } }
  fun setSteady(value: Boolean) { if (value != steady) { steady = value; bind() } }
  fun setMirror(value: Boolean) { if (value != mirror) { mirror = value; bind() } }
  fun setFilter(value: String) { if (value != filter) { filter = value; showFilter() } }

  fun setZoom(ratio: Double) {
    camera?.cameraControl?.setZoomRatio(ratio.toFloat())
  }

  private fun flashMode() = when (flash) {
    "on" -> ImageCapture.FLASH_MODE_ON
    "auto" -> ImageCapture.FLASH_MODE_AUTO
    else -> ImageCapture.FLASH_MODE_OFF
  }

  /** The chosen filter, live on the viewfinder (Android 12 and later). */
  private fun showFilter() {
    if (Build.VERSION.SDK_INT < 31) return
    val m = if (video) null else PhotoFilters.matrix(filter)
    preview.setRenderEffect(m?.let { RenderEffect.createColorFilterEffect(ColorMatrixColorFilter(it)) })
  }

  /** Pinch to zoom, tap to focus. */
  @SuppressLint("ClickableViewAccessibility")
  private fun gestures() {
    val scale = ScaleGestureDetector(context, object : ScaleGestureDetector.SimpleOnScaleGestureListener() {
      override fun onScale(detector: ScaleGestureDetector): Boolean {
        val cam = camera ?: return false
        val now = cam.cameraInfo.zoomState.value?.zoomRatio ?: 1f
        cam.cameraControl.setZoomRatio(now * detector.scaleFactor)
        return true
      }
    })
    preview.setOnTouchListener { _, event ->
      scale.onTouchEvent(event)
      if (event.action == MotionEvent.ACTION_UP && !scale.isInProgress && event.pointerCount == 1 && event.eventTime - event.downTime < 300) {
        val cam = camera
        if (cam != null) {
          val point = preview.meteringPointFactory.createPoint(event.x, event.y)
          try { cam.cameraControl.startFocusAndMetering(FocusMeteringAction.Builder(point).build()) } catch (_: Exception) {}
          val d = resources.displayMetrics.density
          onFocus(mapOf("x" to event.x / d, "y" to event.y / d))
        }
      }
      true
    }
  }

  private fun watchZoom(cam: Camera) {
    val owner = appContext.currentActivity as? LifecycleOwner ?: return
    cam.cameraInfo.zoomState.removeObservers(owner)
    cam.cameraInfo.zoomState.observe(owner) { state ->
      onZoom(mapOf("ratio" to state.zoomRatio, "min" to state.minZoomRatio, "max" to state.maxZoomRatio))
    }
  }

  private fun photoSelector(): ResolutionSelector {
    val builder = ResolutionSelector.Builder()
      // also the sensor's full sizes that the phone lists only as "slow" (many phones keep their real megapixels there)
      .setAllowedResolutionMode(ResolutionSelector.PREFER_HIGHER_RESOLUTION_OVER_CAPTURE_RATE)
    val parts = photoSize.split("x").mapNotNull { it.toIntOrNull() }
    if (parts.size == 2) {
      builder.setResolutionStrategy(ResolutionStrategy(Size(parts[0], parts[1]), ResolutionStrategy.FALLBACK_RULE_CLOSEST_LOWER_THEN_HIGHER))
      builder.setAspectRatioStrategy(if (parts[0] * 9 == parts[1] * 16 || parts[1] * 9 == parts[0] * 16) AspectRatioStrategy.RATIO_16_9_FALLBACK_AUTO_STRATEGY else AspectRatioStrategy.RATIO_4_3_FALLBACK_AUTO_STRATEGY)
    } else {
      builder.setResolutionStrategy(ResolutionStrategy.HIGHEST_AVAILABLE_STRATEGY)
      builder.setAspectRatioStrategy(ratio())
    }
    return builder.build()
  }

  private fun ratio() = if (aspect == "16:9") AspectRatioStrategy.RATIO_16_9_FALLBACK_AUTO_STRATEGY else AspectRatioStrategy.RATIO_4_3_FALLBACK_AUTO_STRATEGY

  private fun quality(): QualitySelector {
    val wanted = when (videoQuality) {
      "2160" -> Quality.UHD
      "1080" -> Quality.FHD
      "720" -> Quality.HD
      else -> Quality.HIGHEST
    }
    return QualitySelector.from(wanted, FallbackStrategy.lowerQualityOrHigherThan(wanted))
  }

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
    camera = null
    try {
      ready.unbindAll()
      camera = try { bindWith(ready, owner, base, steady) } catch (error: Exception) {
        // Some phones refuse steady video or preview: bind again without it.
        if (steady) { ready.unbindAll(); bindWith(ready, owner, base, false) } else throw error
      }
      camera?.let { watchZoom(it); if (video && flash == "on") it.cameraControl.enableTorch(true) }
      showFilter()
    } catch (error: Exception) {
      onCameraError(mapOf("message" to (error.message ?: "camera")))
    }
  }

  private fun bindWith(ready: ProcessCameraProvider, owner: LifecycleOwner, base: CameraSelector, stable: Boolean): Camera {
    val viewfinder = Preview.Builder()
      .setResolutionSelector(ResolutionSelector.Builder().setAspectRatioStrategy(if (video) AspectRatioStrategy.RATIO_16_9_FALLBACK_AUTO_STRATEGY else ratio()).build())
      .apply { if (stable && video) setPreviewStabilizationEnabled(true) }
      .build().also { it.setSurfaceProvider(preview.surfaceProvider) }
    if (video) {
      val recorder = Recorder.Builder().setQualitySelector(quality()).build()
      val capture = VideoCapture.Builder(recorder)
        .apply { if (stable) setVideoStabilizationEnabled(true) }
        .setMirrorMode(if (mirror) MirrorMode.MIRROR_MODE_ON_FRONT_ONLY else MirrorMode.MIRROR_MODE_OFF)
        .build()
      videoCapture = capture
      imageCapture = null
      onModes(mapOf("modes" to emptyList<String>()))
      return ready.bindToLifecycle(owner, base, viewfinder, capture)
    }
    val manager = extensions
    val available = modeMap.filter { manager?.isExtensionAvailable(base, it.value) == true }.keys.toList()
    onModes(mapOf("modes" to available))
    val chosen = modeMap[mode]
    val selector = if (manager != null && chosen != null && mode in available) manager.getExtensionEnabledCameraSelector(base, chosen) else base
    val capture = ImageCapture.Builder()
      .setCaptureMode(ImageCapture.CAPTURE_MODE_MAXIMIZE_QUALITY)
      .setResolutionSelector(photoSelector())
      .setFlashMode(flashMode())
      .setTargetRotation(display?.rotation ?: 0)
      .build()
    imageCapture = capture
    videoCapture = null
    return ready.bindToLifecycle(owner, selector, viewfinder, capture)
  }

  fun takePhoto(promise: Promise) {
    val capture = imageCapture ?: return promise.reject("camera_not_ready", "The camera is not ready yet.", null)
    val target = CaptureStore.scratch(context, "jpg")
    val takenAt = System.currentTimeMillis()
    capture.targetRotation = display?.rotation ?: capture.targetRotation
    val metadata = ImageCapture.Metadata().apply { isReversedHorizontal = facing == "front" && mirror }
    val chosenFilter = filter
    capture.takePicture(ImageCapture.OutputFileOptions.Builder(target).setMetadata(metadata).build(), worker, object : ImageCapture.OnImageSavedCallback {
      override fun onImageSaved(output: ImageCapture.OutputFileResults) {
        try {
          PhotoFilters.apply(target, chosenFilter)
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
