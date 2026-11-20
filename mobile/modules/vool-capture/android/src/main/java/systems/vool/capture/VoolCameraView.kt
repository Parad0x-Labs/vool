package systems.vool.capture

import android.Manifest
import android.content.Context
import android.annotation.SuppressLint
import android.content.pm.PackageManager
import android.graphics.ColorMatrix
import android.graphics.ColorMatrixColorFilter
import android.graphics.RenderEffect
import android.graphics.RuntimeShader
import android.os.Build
import android.util.Size
import android.view.MotionEvent
import android.view.OrientationEventListener
import android.view.Surface
import android.view.ScaleGestureDetector
import android.view.ViewGroup
import androidx.camera.core.Camera
import androidx.camera.core.CameraSelector
import androidx.camera.core.FocusMeteringAction
import androidx.camera.core.ImageAnalysis
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
  private val onTurn by EventDispatcher()
  private val onPage by EventDispatcher()

  /**
   * How the phone is held right now. The app's screen stays upright, so the screen's own rotation
   * never changes; this follows the phone itself, so a photo or video taken sideways is saved sideways.
   */
  private var turn = Surface.ROTATION_0
  private val turnWatch = object : OrientationEventListener(context) {
    override fun onOrientationChanged(degrees: Int) {
      if (degrees == ORIENTATION_UNKNOWN) return
      val next = when (degrees) {
        in 45 until 135 -> Surface.ROTATION_270
        in 135 until 225 -> Surface.ROTATION_180
        in 225 until 315 -> Surface.ROTATION_90
        else -> Surface.ROTATION_0
      }
      if (next == turn) return
      turn = next
      imageCapture?.targetRotation = next
      if (recording == null) videoCapture?.targetRotation = next
      onTurn(mapOf("degrees" to when (next) { Surface.ROTATION_90 -> 90; Surface.ROTATION_180 -> 180; Surface.ROTATION_270 -> 270; else -> 0 }))
    }
  }

  // FIT, not FILL: what the viewfinder shows is exactly what the photo holds.
  private val preview = PreviewView(context).apply {
    implementationMode = PreviewView.ImplementationMode.COMPATIBLE
    scaleType = PreviewView.ScaleType.FIT_CENTER
  }
  private val worker = Executors.newSingleThreadExecutor()
  private val looker = Executors.newSingleThreadExecutor()
  private val outline = PageOutline(context).apply { onFound = { found -> onPage(mapOf("found" to found)) } }
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
  /** VOOL's own photo mode, made after the shot: "" | enhance | beauty1-3 | doc-color | doc-gray | doc-bw. */
  private var edit = ""
  private val document get() = edit.startsWith("doc-")

  init {
    addView(preview, ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
    addView(outline, ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
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
  fun setEdit(value: String) {
    val next = if (value in ImageEdits.edits) value else ""
    if (next == edit) return
    val rebind = next.startsWith("doc-") != document
    edit = next
    if (rebind) bind() else showFilter()
  }

  fun setZoom(ratio: Double) {
    camera?.cameraControl?.setZoomRatio(ratio.toFloat())
  }

  private fun flashMode() = when (flash) {
    "on" -> ImageCapture.FLASH_MODE_ON
    "auto" -> ImageCapture.FLASH_MODE_AUTO
    else -> ImageCapture.FLASH_MODE_OFF
  }

  /**
   * The chosen look and mode, live on the viewfinder (Android 12 and later; Beauty's smoothing from
   * Android 13). The live view is a close preview: the saved photo is made at full size after the shot.
   */
  private fun showFilter() {
    if (Build.VERSION.SDK_INT < 31) return
    if (video || document) { preview.setRenderEffect(null); return }
    val look = PhotoFilters.matrix(filter)
    val boost = if (edit == "enhance") ColorMatrix().apply { setSaturation(1.08f); postConcat(ColorMatrix(floatArrayOf(
      1.06f, 0f, 0f, 0f, -7.7f, 0f, 1.06f, 0f, 0f, -7.7f, 0f, 0f, 1.06f, 0f, -7.7f, 0f, 0f, 0f, 1f, 0f))) } else null
    val color = when {
      look != null && boost != null -> ColorMatrix(boost).apply { postConcat(look) }
      else -> look ?: boost
    }
    var effect = color?.let { RenderEffect.createColorFilterEffect(ColorMatrixColorFilter(it)) }
    beautyEffect()?.let { skin -> effect = effect?.let { RenderEffect.createChainEffect(it, skin) } ?: skin }
    preview.setRenderEffect(effect)
  }

  private fun beautyEffect(): RenderEffect? {
    if (Build.VERSION.SDK_INT < 33 || !edit.startsWith("beauty")) return null
    val level = edit.removePrefix("beauty").toIntOrNull()?.coerceIn(1, 3) ?: 2
    return try {
      val shader = RuntimeShader(BEAUTY_SHADER)
      shader.setFloatUniform("amount", floatArrayOf(0.45f, 0.65f, 0.85f)[level - 1])
      shader.setFloatUniform("radius", 3f * resources.displayMetrics.density)
      RenderEffect.createRuntimeShaderEffect(shader, "content")
    } catch (_: Throwable) { null }
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
    if (turnWatch.canDetectOrientation()) turnWatch.enable()
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
    turnWatch.disable()
    outline.clear()
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
      if (!document) outline.clear()
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
        .setTargetRotation(turn)
        .build()
      videoCapture = capture
      imageCapture = null
      onModes(mapOf("modes" to emptyList<String>()))
      return ready.bindToLifecycle(owner, base, viewfinder, capture)
    }
    val manager = extensions
    val available = modeMap.filter { manager?.isExtensionAvailable(base, it.value) == true }.keys.toList()
    onModes(mapOf("modes" to available))
    if (document) {
      // Document: plain camera plus a small brightness-only frame for the live page outline.
      val capture = ImageCapture.Builder()
        .setCaptureMode(ImageCapture.CAPTURE_MODE_MAXIMIZE_QUALITY)
        .setResolutionSelector(photoSelector())
        .setFlashMode(flashMode())
        .setTargetRotation(turn)
        .build()
      val analysis = ImageAnalysis.Builder()
        .setResolutionSelector(ResolutionSelector.Builder().setAspectRatioStrategy(ratio())
          .setResolutionStrategy(ResolutionStrategy(Size(640, 480), ResolutionStrategy.FALLBACK_RULE_CLOSEST_HIGHER_THEN_LOWER)).build())
        .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
        .build().also { it.setAnalyzer(looker, outline.analyzer) }
      imageCapture = capture
      videoCapture = null
      return try { ready.bindToLifecycle(owner, base, viewfinder, capture, analysis) } catch (error: Exception) {
        // A phone that cannot run three streams at once still scans, without the live outline.
        ready.unbindAll(); ready.bindToLifecycle(owner, base, viewfinder, capture)
      }
    }
    val chosen = modeMap[mode]
    val selector = if (manager != null && chosen != null && mode in available) manager.getExtensionEnabledCameraSelector(base, chosen) else base
    val capture = ImageCapture.Builder()
      .setCaptureMode(ImageCapture.CAPTURE_MODE_MAXIMIZE_QUALITY)
      .setResolutionSelector(photoSelector())
      .setFlashMode(flashMode())
      .setTargetRotation(turn)
      .build()
    imageCapture = capture
    videoCapture = null
    return ready.bindToLifecycle(owner, selector, viewfinder, capture)
  }

  fun takePhoto(promise: Promise) {
    val capture = imageCapture ?: return promise.reject("camera_not_ready", "The camera is not ready yet.", null)
    val target = CaptureStore.scratch(context, "jpg")
    val takenAt = System.currentTimeMillis()
    capture.targetRotation = turn
    val metadata = ImageCapture.Metadata().apply { isReversedHorizontal = facing == "front" && mirror }
    // The look and the mode travel with the shot ("look|mode"); they are made after it, in the saver.
    val chosenFilter = if (edit.isEmpty()) filter else "${if (document) "none" else filter}|$edit"
    // The shutter is free again as soon as the picture is taken: writing, the filter, compression and
    // locking it away all happen behind, in order, so shots in a row never wait for each other.
    val answered = java.util.concurrent.atomic.AtomicBoolean(false)
    fun answer() { if (answered.compareAndSet(false, true)) promise.resolve(mapOf("saving" to true, "kind" to "photo")) }
    fun lost(code: String, message: String, error: Exception?) {
      target.delete()
      if (answered.compareAndSet(false, true)) promise.reject(code, message, error)
      else post { onCaptured(mapOf("ok" to false, "kind" to "photo")) }
    }
    CaptureStore.shot()
    capture.takePicture(ImageCapture.OutputFileOptions.Builder(target).setMetadata(metadata).build(), worker, object : ImageCapture.OnImageSavedCallback {
      override fun onCaptureStarted() {
        CaptureStore.shot()
        answer()
      }

      override fun onImageSaved(output: ImageCapture.OutputFileResults) {
        try {
          CaptureStore.keep(context, target, "image/jpeg", "photo", takenAt, chosenFilter)
          answer()
        } catch (error: Exception) {
          lost("capture_not_stored", error.message ?: "The photo could not be stored.", error)
        }
      }

      override fun onError(error: ImageCaptureException) {
        lost("photo_failed", error.message ?: "The photo could not be taken.", error)
      }
    })
  }

  fun startVideo(promise: Promise) {
    val capture = videoCapture ?: return promise.reject("camera_not_ready", "The camera is not ready yet.", null)
    if (recording != null) return promise.resolve(true)
    val target = CaptureStore.scratch(context, "mp4")
    val takenAt = System.currentTimeMillis()
    capture.targetRotation = turn
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

/**
 * Live Beauty on the viewfinder: skin (by colour) is softened with an edge-keeping blur and lifted a
 * little. A close preview of what the saver makes at full size with OpenCV after the shot.
 */
private const val BEAUTY_SHADER = """
uniform shader content;
uniform float amount;
uniform float radius;
half4 main(float2 p) {
  half4 c = content.eval(p);
  float y = dot(float3(c.rgb), float3(0.299, 0.587, 0.114));
  float cr = (float(c.r) - y) * 0.713 + 0.5;
  float cb = (float(c.b) - y) * 0.564 + 0.5;
  float outside = max(max(0.52 - cr, cr - 0.68), max(0.30 - cb, cb - 0.50));
  float skin = 1.0 - smoothstep(0.0, 0.03, outside);
  if (skin <= 0.0) { return c; }
  float3 sum = float3(c.rgb);
  float total = 1.0;
  for (int i = 0; i < 12; i++) {
    float a = float(i) * 0.5236;
    float2 d = float2(cos(a), sin(a));
    for (int r = 1; r <= 2; r++) {
      float3 s = float3(content.eval(p + d * radius * float(r)).rgb);
      float3 diff = s - float3(c.rgb);
      float w = exp(-dot(diff, diff) * 60.0);
      sum += s * w;
      total += w;
    }
  }
  float m = skin * amount;
  float3 outc = mix(float3(c.rgb), sum / total, m) + m * 0.05;
  return half4(half3(clamp(outc, 0.0, 1.0)), c.a);
}
"""
