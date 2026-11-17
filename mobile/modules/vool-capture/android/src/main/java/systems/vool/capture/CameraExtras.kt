package systems.vool.capture

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.ColorMatrix
import android.graphics.ImageFormat
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.os.Build
import android.os.SystemClock
import androidx.exifinterface.media.ExifInterface
import java.io.File

/**
 * Remembers that the VOOL Camera icon or quick settings tile just opened VOOL, so the app opens the
 * camera at once instead of asking for the fingerprint first. Only the camera opens this way: chats,
 * wallet and settings stay behind VOOL's lock.
 */
object CameraLaunch {
  @Volatile private var at = 0L

  fun mark() { at = SystemClock.elapsedRealtime() }

  /** True for a few seconds after the icon or tile was used; reading it does not clear it. */
  fun recent(): Boolean = at != 0L && SystemClock.elapsedRealtime() - at < 5_000

  fun clear() { at = 0L }
}

/** The camera's own choices (flash, timer, grid, filter ...), kept as one small JSON text the app shapes. */
object CameraPrefs {
  private const val FILE = "vool_camera"
  private const val KEY = "prefs"

  fun get(context: Context): String = context.getSharedPreferences(FILE, Context.MODE_PRIVATE).getString(KEY, "") ?: ""

  fun set(context: Context, json: String) {
    context.getSharedPreferences(FILE, Context.MODE_PRIVATE).edit().putString(KEY, json.take(4_000)).apply()
  }
}

/** The photo sizes this phone hands to apps for one camera, largest first, and the sensor's own largest. */
object PhotoSizes {
  fun of(context: Context, facing: String): Map<String, Any> {
    val manager = context.getSystemService(CameraManager::class.java) ?: return mapOf("sizes" to emptyList<String>(), "sensorMax" to "")
    val wanted = if (facing == "front") CameraCharacteristics.LENS_FACING_FRONT else CameraCharacteristics.LENS_FACING_BACK
    val id = manager.cameraIdList.firstOrNull { manager.getCameraCharacteristics(it).get(CameraCharacteristics.LENS_FACING) == wanted }
      ?: return mapOf("sizes" to emptyList<String>(), "sensorMax" to "")
    val info = manager.getCameraCharacteristics(id)
    val map = info.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)
    val sizes = ((map?.getOutputSizes(ImageFormat.JPEG)?.toList() ?: emptyList()) + (map?.getHighResolutionOutputSizes(ImageFormat.JPEG)?.toList() ?: emptyList()))
      .distinct().sortedByDescending { it.width.toLong() * it.height }
    var sensorMax = ""
    if (Build.VERSION.SDK_INT >= 31) {
      val full = info.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP_MAXIMUM_RESOLUTION)
      val biggest = full?.getOutputSizes(ImageFormat.JPEG)?.maxByOrNull { it.width.toLong() * it.height }
        ?: full?.getOutputSizes(ImageFormat.RAW_SENSOR)?.maxByOrNull { it.width.toLong() * it.height }
      if (biggest != null) sensorMax = "${biggest.width}x${biggest.height}"
    }
    if (sensorMax.isEmpty()) {
      info.get(CameraCharacteristics.SENSOR_INFO_PIXEL_ARRAY_SIZE)?.let { sensorMax = "${it.width}x${it.height}" }
    }
    return mapOf("sizes" to sizes.take(12).map { "${it.width}x${it.height}" }, "sensorMax" to sensorMax)
  }
}

/**
 * Colour filters for photos. The viewfinder shows the same matrix live (Android 12 and later); the
 * saved photo gets it after the shot, before it is locked away. Video keeps its true colours.
 */
object PhotoFilters {
  val names = listOf("none", "warm", "cool", "vivid", "fade", "mono", "noir")

  fun matrix(name: String): ColorMatrix? {
    val m = ColorMatrix()
    when (name) {
      "warm" -> m.set(floatArrayOf(1.08f, 0f, 0f, 0f, 8f, 0f, 1.01f, 0f, 0f, 2f, 0f, 0f, 0.9f, 0f, 0f, 0f, 0f, 0f, 1f, 0f))
      "cool" -> m.set(floatArrayOf(0.92f, 0f, 0f, 0f, 0f, 0f, 1.0f, 0f, 0f, 2f, 0f, 0f, 1.08f, 0f, 8f, 0f, 0f, 0f, 1f, 0f))
      "vivid" -> { m.setSaturation(1.35f); m.postConcat(contrast(1.08f)) }
      "fade" -> { m.setSaturation(0.8f); m.postConcat(contrast(0.86f)); m.postConcat(ColorMatrix(floatArrayOf(1f, 0f, 0f, 0f, 14f, 0f, 1f, 0f, 0f, 12f, 0f, 0f, 1f, 0f, 10f, 0f, 0f, 0f, 1f, 0f))) }
      "mono" -> m.setSaturation(0f)
      "noir" -> { m.setSaturation(0f); m.postConcat(contrast(1.3f)) }
      else -> return null
    }
    return m
  }

  private fun contrast(c: Float): ColorMatrix {
    val t = 128f * (1f - c)
    return ColorMatrix(floatArrayOf(c, 0f, 0f, 0f, t, 0f, c, 0f, 0f, t, 0f, 0f, c, 0f, t, 0f, 0f, 0f, 1f, 0f))
  }

  private val keepTags = listOf(
    ExifInterface.TAG_ORIENTATION, ExifInterface.TAG_DATETIME, ExifInterface.TAG_DATETIME_ORIGINAL, ExifInterface.TAG_DATETIME_DIGITIZED,
    ExifInterface.TAG_OFFSET_TIME, ExifInterface.TAG_OFFSET_TIME_ORIGINAL, ExifInterface.TAG_SUBSEC_TIME_ORIGINAL,
    ExifInterface.TAG_MAKE, ExifInterface.TAG_MODEL, ExifInterface.TAG_EXPOSURE_TIME, ExifInterface.TAG_F_NUMBER,
    ExifInterface.TAG_PHOTOGRAPHIC_SENSITIVITY, ExifInterface.TAG_FOCAL_LENGTH, ExifInterface.TAG_FOCAL_LENGTH_IN_35MM_FILM,
    ExifInterface.TAG_FLASH, ExifInterface.TAG_WHITE_BALANCE, ExifInterface.TAG_EXPOSURE_BIAS_VALUE, ExifInterface.TAG_APERTURE_VALUE,
  )

  /**
   * Puts the filter into a saved JPEG, in place, keeping its date, orientation and camera details.
   * Works in strips so a large photo does not need a second copy in memory. Any trouble (too little
   * memory, a file it cannot read) leaves the photo exactly as the camera took it.
   */
  fun apply(file: File, name: String) {
    val m = matrix(name)?.array ?: return
    try {
      val old = ExifInterface(file.absolutePath)
      val tags = keepTags.mapNotNull { tag -> old.getAttribute(tag)?.let { tag to it } }
      val bitmap = BitmapFactory.decodeFile(file.absolutePath, BitmapFactory.Options().apply { inMutable = true }) ?: return
      val width = bitmap.width
      val rows = 64
      val line = IntArray(width * rows)
      var y = 0
      while (y < bitmap.height) {
        val h = minOf(rows, bitmap.height - y)
        bitmap.getPixels(line, 0, width, 0, y, width, h)
        for (i in 0 until width * h) {
          val p = line[i]
          val a = p ushr 24; val r = (p shr 16) and 255; val g = (p shr 8) and 255; val b = p and 255
          val nr = (m[0] * r + m[1] * g + m[2] * b + m[3] * a + m[4]).toInt().coerceIn(0, 255)
          val ng = (m[5] * r + m[6] * g + m[7] * b + m[8] * a + m[9]).toInt().coerceIn(0, 255)
          val nb = (m[10] * r + m[11] * g + m[12] * b + m[13] * a + m[14]).toInt().coerceIn(0, 255)
          line[i] = (a shl 24) or (nr shl 16) or (ng shl 8) or nb
        }
        bitmap.setPixels(line, 0, width, 0, y, width, h)
        y += h
      }
      val out = File(file.parentFile, file.name + ".filter")
      out.outputStream().use { bitmap.compress(Bitmap.CompressFormat.JPEG, 95, it) }
      bitmap.recycle()
      val fresh = ExifInterface(out.absolutePath)
      for ((tag, value) in tags) fresh.setAttribute(tag, value)
      fresh.saveAttributes()
      if (!out.renameTo(file)) out.delete()
    } catch (_: Throwable) {
      File(file.parentFile, file.name + ".filter").delete()
    }
  }
}
