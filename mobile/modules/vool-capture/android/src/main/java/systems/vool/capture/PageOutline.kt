package systems.vool.capture

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.os.SystemClock
import android.view.View
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import org.opencv.core.CvType
import org.opencv.core.Mat

/**
 * Document mode's live outline: a few times a second a small frame from the camera (brightness only,
 * kept in memory, never saved) is searched for a page, and the page's edge is drawn over the
 * viewfinder. The saved photo is found and flattened again at full size after the shot.
 */
class PageOutline(context: Context) : View(context) {
  /** Corners as fractions of the upright picture (x0, y0, ... x3, y3), or null when no page is seen. */
  @Volatile private var corners: FloatArray? = null
  private var shown: FloatArray? = null
  private var lastSeen = 0L
  private var pictureAspect = 3f / 4f
  var onFound: ((Boolean) -> Unit)? = null
  private var found = false

  private val line = Paint(Paint.ANTI_ALIAS_FLAG).apply {
    style = Paint.Style.STROKE; strokeWidth = 3f * resources.displayMetrics.density
    color = Color.rgb(240, 179, 90); strokeJoin = Paint.Join.ROUND
  }
  private val fill = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL; color = Color.argb(46, 240, 179, 90) }
  private val path = Path()

  init { setWillNotDraw(false) }

  /** Searches one camera frame; called on the analysis thread, at most about 4 times a second. */
  val analyzer = ImageAnalysis.Analyzer { image -> look(image) }
  private var lastLook = 0L

  private fun look(image: ImageProxy) {
    try {
      val now = SystemClock.elapsedRealtime()
      if (now - lastLook < 250 || !ImageEdits.ready()) return
      lastLook = now
      val plane = image.planes[0]
      val w = image.width
      val h = image.height
      val gray = Mat(h, w, CvType.CV_8UC1, plane.buffer, plane.rowStride.toLong())
      val page = try { ImageEdits.findPage(gray) } finally { gray.release() }
      val turn = image.imageInfo.rotationDegrees
      val (uw, uh) = if (turn == 90 || turn == 270) h to w else w to h
      val next = page?.let { p ->
        FloatArray(8).also { out ->
          p.forEachIndexed { i, point ->
            val (x, y) = when (turn) {
              90 -> (h - point.y) to point.x
              180 -> (w - point.x) to (h - point.y)
              270 -> point.y to (w - point.x)
              else -> point.x to point.y
            }
            out[i * 2] = (x / uw).toFloat(); out[i * 2 + 1] = (y / uh).toFloat()
          }
        }
      }
      if (next != null) lastSeen = now
      // A page missed for one look keeps its outline a moment, so it does not flicker.
      if (next != null || now - lastSeen > 700) corners = next
      pictureAspect = uw.toFloat() / uh
      post { invalidate() }
    } catch (_: Throwable) {
      // a frame that cannot be read is skipped
    } finally {
      image.close()
    }
  }

  fun clear() { corners = null; shown = null; post { invalidate() } }

  override fun onDraw(canvas: Canvas) {
    val target = corners
    val isFound = target != null
    if (isFound != found) { found = isFound; onFound?.invoke(isFound) }
    if (target == null) { shown = null; return }
    // Ease toward the new corners.
    val now = shown?.let { old -> FloatArray(8) { i -> old[i] + (target[i] - old[i]) * 0.5f } } ?: target.copyOf()
    shown = now
    // Where the picture sits in the viewfinder (fitted, centred).
    val vw = width.toFloat(); val vh = height.toFloat()
    val (pw, ph) = if (vw / vh > pictureAspect) vh * pictureAspect to vh else vw to vw / pictureAspect
    val left = (vw - pw) / 2; val top = (vh - ph) / 2
    path.reset()
    for (i in 0 until 4) {
      val x = left + now[i * 2] * pw; val y = top + now[i * 2 + 1] * ph
      if (i == 0) path.moveTo(x, y) else path.lineTo(x, y)
    }
    path.close()
    canvas.drawPath(path, fill)
    canvas.drawPath(path, line)
    if (!now.contentEquals(target)) postInvalidateOnAnimation()
  }
}
