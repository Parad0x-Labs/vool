package systems.vool.capture

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.util.Log
import org.opencv.android.OpenCVLoader
import org.opencv.android.Utils
import org.opencv.core.Core
import org.opencv.core.CvType
import org.opencv.core.Mat
import org.opencv.core.MatOfInt
import org.opencv.core.MatOfFloat
import org.opencv.core.MatOfPoint
import org.opencv.core.MatOfPoint2f
import org.opencv.core.Point
import org.opencv.core.Scalar
import org.opencv.core.Size
import org.opencv.imgproc.Imgproc
import java.io.File
import kotlin.math.hypot
import kotlin.math.ln
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow
import kotlin.math.roundToInt

/**
 * VOOL Camera's own photo modes, made on the phone after the shot: Enhance, Beauty and Document.
 *
 * They run on the temporary private copy inside CaptureStore, before compression and before it is
 * locked away, exactly where the colour looks already run. Nothing is written anywhere else and
 * nothing leaves the phone. The work is done with OpenCV (Apache-2.0); see
 * docs/THIRD_PARTY_LICENSES.md.
 *
 * A capture's "filter" text carries the choice as "look" or "look|edit", for example "warm|beauty2"
 * or "none|doc-bw", so a capture waiting from an older version still finishes the same way.
 *
 * Any trouble (OpenCV missing, too little memory, a picture it cannot read) keeps the photo as the
 * camera took it, with only the colour look applied, and says so in the log. A mode never loses a shot.
 */
object ImageEdits {
  private const val LOG = "VoolCapture"
  val edits = setOf("enhance", "beauty1", "beauty2", "beauty3", "doc-color", "doc-gray", "doc-bw")

  @Volatile private var loaded: Boolean? = null

  /** Loads OpenCV once. False when this phone cannot load it (then the modes are skipped, never the photo). */
  fun ready(): Boolean = synchronized(this) {
    loaded ?: (try { OpenCVLoader.initLocal() } catch (_: Throwable) { false }).also {
      loaded = it
      if (!it) Log.w(LOG, "edit: OpenCV could not be loaded; photo modes are off")
    }
  }

  /** Splits "look|edit" into its two parts; a plain look has no edit. */
  fun parse(spec: String): Pair<String, String> {
    val parts = spec.split("|", limit = 2)
    return parts[0].ifEmpty { "none" } to parts.getOrElse(1) { "" }
  }

  /** Changes the saved JPEG in place: the mode first, then the colour look. */
  fun apply(file: File, spec: String) {
    val (look, edit) = parse(spec)
    if (edit !in edits) {
      if (look != "none") PhotoFilters.apply(file, look)
      return
    }
    if (!ready()) {
      if (look != "none") PhotoFilters.apply(file, look)
      return
    }
    val out = File(file.parentFile, file.name + ".edit")
    try {
      val tags = PhotoFilters.tagsOf(file)
      val done = if (edit.startsWith("doc-")) document(file, out, edit.removePrefix("doc-")) else photo(file, out, edit, look)
      if (!done) {
        out.delete()
        if (look != "none" && !edit.startsWith("doc-")) PhotoFilters.apply(file, look)
        return
      }
      PhotoFilters.putTags(out, tags)
      if (!out.renameTo(file)) { out.delete(); throw IllegalStateException("not replaced") }
    } catch (error: Throwable) {
      out.delete()
      Log.w(LOG, "edit: $edit failed (${error.javaClass.simpleName}); the photo is kept as taken")
      if (look != "none" && !edit.startsWith("doc-")) PhotoFilters.apply(file, look)
    }
  }

  // ---------------------------------------------------------------- Enhance and Beauty

  private fun photo(src: File, out: File, edit: String, look: String): Boolean {
    val bitmap = BitmapFactory.decodeFile(src.absolutePath, BitmapFactory.Options().apply {
      inMutable = true; inPreferredConfig = Bitmap.Config.ARGB_8888
    }) ?: return false
    try {
      val long = max(bitmap.width, bitmap.height)
      val lookMatrix = PhotoFilters.matrix(look)?.array?.let { lookTransform(it) }
      if (edit == "enhance") {
        val lut = toneCurve(bitmap)
        val sigma = max(1.0, long / 4000.0)
        strips(bitmap, (sigma * 4).roundToInt() + 8) { rgb -> enhance(rgb, lut, sigma); lookMatrix?.let { Core.transform(rgb, rgb, it) } }
        lut.release()
      } else {
        val level = edit.removePrefix("beauty").toIntOrNull()?.coerceIn(1, 3) ?: 2
        // Skin is smoothed at about face scale: the larger the photo, the further down it is worked on.
        val factor = max(1.0, long / 1600.0)
        strips(bitmap, (factor * 24).roundToInt() + 8) { rgb -> beauty(rgb, level, factor); lookMatrix?.let { Core.transform(rgb, rgb, it) } }
      }
      lookMatrix?.release()
      out.outputStream().use { bitmap.compress(Bitmap.CompressFormat.JPEG, 95, it) }
      return true
    } finally {
      bitmap.recycle()
    }
  }

  /** The colour look's 4x5 matrix as OpenCV's 3x4 colour transform. */
  private fun lookTransform(m: FloatArray): Mat {
    val t = Mat(3, 4, CvType.CV_32F)
    t.put(0, 0, floatArrayOf(m[0], m[1], m[2], m[4], m[5], m[6], m[7], m[9], m[10], m[11], m[12], m[14]))
    return t
  }

  /**
   * Works through a large photo in bands of rows, each with a margin above and below so the edges
   * match, and never holds a second full-size copy. A band is written back only after the next band
   * has been read, so no band ever reads pixels that were already changed.
   */
  private fun strips(bitmap: Bitmap, margin: Int, work: (Mat) -> Unit) {
    val w = bitmap.width
    val h = bitmap.height
    val rows = max(512, margin * 4)
    val buffers = arrayOf(IntArray(w * rows), IntArray(w * rows))
    var which = 0
    var pendingY = -1
    var pendingRows = 0
    val rgba = Mat()
    val rgb = Mat()
    var piece: Bitmap? = null
    val input = IntArray(w * (rows + 2 * margin))
    try {
      var y = 0
      while (y < h) {
        val inner = min(rows, h - y)
        val top = max(0, y - margin)
        val bottom = min(h, y + inner + margin)
        val tall = bottom - top
        bitmap.getPixels(input, 0, w, 0, top, w, tall)
        if (pendingY >= 0) bitmap.setPixels(buffers[1 - which], 0, w, 0, pendingY, w, pendingRows)
        if (piece == null || piece.height != tall) { piece?.recycle(); piece = Bitmap.createBitmap(w, tall, Bitmap.Config.ARGB_8888) }
        piece.setPixels(input, 0, w, 0, 0, w, tall)
        Utils.bitmapToMat(piece, rgba)
        Imgproc.cvtColor(rgba, rgb, Imgproc.COLOR_RGBA2RGB)
        work(rgb)
        Imgproc.cvtColor(rgb, rgba, Imgproc.COLOR_RGB2RGBA)
        Utils.matToBitmap(rgba, piece)
        piece.getPixels(buffers[which], 0, w, 0, y - top, w, inner)
        pendingY = y
        pendingRows = inner
        which = 1 - which
        y += inner
      }
      if (pendingY >= 0) bitmap.setPixels(buffers[1 - which], 0, w, 0, pendingY, w, pendingRows)
    } finally {
      piece?.recycle()
      rgba.release(); rgb.release()
    }
  }

  /** A gentle tone curve from the whole photo: levels, mid-tone brightness and a soft S-curve. */
  private fun toneCurve(bitmap: Bitmap): Mat {
    val scale = min(1.0, 800.0 / max(bitmap.width, bitmap.height))
    val small = Bitmap.createScaledBitmap(bitmap, max(1, (bitmap.width * scale).toInt()), max(1, (bitmap.height * scale).toInt()), true)
    val rgba = Mat(); val gray = Mat(); val hist = Mat()
    Utils.bitmapToMat(small, rgba)
    if (small !== bitmap) small.recycle()
    Imgproc.cvtColor(rgba, gray, Imgproc.COLOR_RGBA2GRAY)
    Imgproc.calcHist(listOf(gray), MatOfInt(0), Mat(), hist, MatOfInt(256), MatOfFloat(0f, 256f))
    val counts = FloatArray(256).also { hist.get(0, 0, it) }
    val total = counts.sum().coerceAtLeast(1f)
    fun percentile(p: Float): Int { var sum = 0f; for (i in 0 until 256) { sum += counts[i]; if (sum >= total * p) return i }; return 255 }
    val lo = percentile(0.005f).coerceAtMost(60)
    val hi = percentile(0.995f).coerceAtLeast(lo + 80).coerceAtMost(255)
    val median = percentile(0.5f)
    rgba.release(); gray.release(); hist.release()
    val stretchedMid = ((median - lo).toDouble() / (hi - lo)).coerceIn(0.05, 0.95)
    val gamma = (ln(0.46) / ln(stretchedMid)).coerceIn(0.8, 1.25)
    val lut = Mat(1, 256, CvType.CV_8U)
    val values = ByteArray(256)
    for (x in 0 until 256) {
      var v = ((x - lo).toDouble() / (hi - lo)).coerceIn(0.0, 1.0)
      v = 0.7 * v + 0.3 * (x / 255.0)
      v = v.pow(gamma)
      v += 0.3 * v * (1 - v) * (2 * v - 1)
      values[x] = (v * 255).roundToInt().coerceIn(0, 255).toByte()
    }
    lut.put(0, 0, values)
    return lut
  }

  /** Enhance: tone curve on brightness, chroma noise smoothed, fine luminance noise calmed, then sharpened. */
  private fun enhance(rgb: Mat, lut: Mat, sigma: Double) {
    val ycc = Mat()
    Imgproc.cvtColor(rgb, ycc, Imgproc.COLOR_RGB2YCrCb)
    val ch = ArrayList<Mat>(3)
    Core.split(ycc, ch)
    val y = ch[0]
    Core.LUT(y, lut, y)
    val calm = Mat()
    Imgproc.bilateralFilter(y, calm, 5, 12.0, 3.0)
    val blur = Mat()
    Imgproc.GaussianBlur(calm, blur, Size(0.0, 0.0), 1.2 * sigma)
    Core.addWeighted(calm, 1.6, blur, -0.6, 0.0, y)
    for (i in 1..2) {
      Imgproc.GaussianBlur(ch[i], ch[i], Size(0.0, 0.0), 1.5 * sigma)
      ch[i].convertTo(ch[i], -1, 1.06, -128 * 0.06)
    }
    Core.merge(ch, ycc)
    Imgproc.cvtColor(ycc, rgb, Imgproc.COLOR_YCrCb2RGB)
    ch.forEach { it.release() }
    ycc.release(); calm.release(); blur.release()
  }

  /**
   * Beauty: skin (found by its colour) is smoothed with an edge-keeping filter, a little lighter and
   * brighter; eyes, hair, lips and the background keep their detail. Level 1 to 3.
   */
  private fun beauty(rgb: Mat, level: Int, factor: Double) {
    val strength = doubleArrayOf(0.45, 0.65, 0.85)[level - 1]
    val small = Mat()
    Imgproc.resize(rgb, small, Size(max(1.0, rgb.cols() / factor), max(1.0, rgb.rows() / factor)), 0.0, 0.0, Imgproc.INTER_AREA)
    val smooth = Mat()
    Imgproc.bilateralFilter(small, smooth, 9, 20.0 + 10 * level, 6.0)
    if (level == 3) { val again = Mat(); Imgproc.bilateralFilter(smooth, again, 9, 30.0, 6.0); again.copyTo(smooth); again.release() }
    // Skin by colour (YCrCb), cleaned of specks and feathered so there is no visible edge.
    val ycc = Mat(); val mask = Mat()
    Imgproc.cvtColor(small, ycc, Imgproc.COLOR_RGB2YCrCb)
    Core.inRange(ycc, Scalar(40.0, 133.0, 77.0), Scalar(255.0, 173.0, 127.0), mask)
    val kernel = Imgproc.getStructuringElement(Imgproc.MORPH_ELLIPSE, Size(5.0, 5.0))
    Imgproc.morphologyEx(mask, mask, Imgproc.MORPH_OPEN, kernel)
    Imgproc.GaussianBlur(mask, mask, Size(0.0, 0.0), 4.0)
    val full = rgb.size()
    val up = Mat(); val maskUp = Mat()
    Imgproc.resize(smooth, up, full, 0.0, 0.0, Imgproc.INTER_LINEAR)
    Imgproc.resize(mask, maskUp, full, 0.0, 0.0, Imgproc.INTER_LINEAR)
    val f = Mat(); val s = Mat(); val m = Mat(); val m3 = Mat()
    rgb.convertTo(f, CvType.CV_32FC3)
    up.convertTo(s, CvType.CV_32FC3)
    maskUp.convertTo(m, CvType.CV_32F, strength / 255.0)
    Core.merge(listOf(m, m, m), m3)
    // out = f + (s - f) * mask, then a lift on skin (whitening) and a touch of overall brightness
    Core.subtract(s, f, s)
    Core.multiply(s, m3, s)
    Core.add(f, s, f)
    val lift = 6.0 + 4.0 * level
    Core.scaleAdd(m3, lift / strength, f, f)
    f.convertTo(rgb, CvType.CV_8UC3, 1.0, 2.0 + level)
    listOf(small, smooth, ycc, mask, kernel, up, maskUp, f, s, m, m3).forEach { it.release() }
  }

  // ---------------------------------------------------------------- Document

  /**
   * Document: finds the page, flattens it to a straight rectangle, and cleans it (shadows and paper
   * tint removed). Style "color", "gray" or "bw". When no page edge is found the whole photo is
   * cleaned, so the shot is never lost.
   */
  private fun document(src: File, out: File, style: String): Boolean {
    val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    BitmapFactory.decodeFile(src.absolutePath, bounds)
    val long = max(bounds.outWidth, bounds.outHeight)
    if (long <= 0) return false
    var sample = 1
    while (long / (sample * 2) >= 3000) sample *= 2
    val bitmap = BitmapFactory.decodeFile(src.absolutePath, BitmapFactory.Options().apply {
      inSampleSize = sample; inPreferredConfig = Bitmap.Config.ARGB_8888
    }) ?: return false
    val rgba = Mat(); val rgb = Mat()
    Utils.bitmapToMat(bitmap, rgba)
    bitmap.recycle()
    Imgproc.cvtColor(rgba, rgb, Imgproc.COLOR_RGBA2RGB)
    rgba.release()
    val corners = findPage(rgb)
    val flat = if (corners != null) flatten(rgb, corners) else rgb.clone()
    rgb.release()
    val clean = clean(flat, style)
    flat.release()
    val result = Mat()
    Imgproc.cvtColor(clean, result, if (clean.channels() == 1) Imgproc.COLOR_GRAY2RGBA else Imgproc.COLOR_RGB2RGBA)
    clean.release()
    val page = Bitmap.createBitmap(result.cols(), result.rows(), Bitmap.Config.ARGB_8888)
    Utils.matToBitmap(result, page)
    result.release()
    try {
      out.outputStream().use { page.compress(Bitmap.CompressFormat.JPEG, 92, it) }
    } finally {
      page.recycle()
    }
    return true
  }

  /**
   * The four corners of the page in [image] (RGB or one-channel), top-left first and clockwise, or
   * null when no page fills at least a fifth of the picture. Also used for the live outline.
   */
  fun findPage(image: Mat): Array<Point>? {
    val long = max(image.cols(), image.rows()).toDouble()
    val scale = min(1.0, 800.0 / long)
    val small = Mat(); val gray = Mat(); val edges = Mat()
    Imgproc.resize(image, small, Size(image.cols() * scale, image.rows() * scale), 0.0, 0.0, Imgproc.INTER_AREA)
    if (small.channels() == 3) Imgproc.cvtColor(small, gray, Imgproc.COLOR_RGB2GRAY) else small.copyTo(gray)
    Imgproc.GaussianBlur(gray, gray, Size(5.0, 5.0), 0.0)
    // Closing first hides the writing, so the page's own edge is what stays.
    val kernel = Imgproc.getStructuringElement(Imgproc.MORPH_RECT, Size(9.0, 9.0))
    Imgproc.morphologyEx(gray, gray, Imgproc.MORPH_CLOSE, kernel, Point(-1.0, -1.0), 2)
    Imgproc.Canny(gray, edges, 30.0, 90.0)
    Imgproc.dilate(edges, edges, Imgproc.getStructuringElement(Imgproc.MORPH_RECT, Size(3.0, 3.0)))
    val contours = ArrayList<MatOfPoint>()
    val hierarchy = Mat()
    Imgproc.findContours(edges, contours, hierarchy, Imgproc.RETR_LIST, Imgproc.CHAIN_APPROX_SIMPLE)
    val area = small.cols().toDouble() * small.rows()
    var found: Array<Point>? = null
    for (contour in contours.sortedByDescending { Imgproc.contourArea(it) }.take(8)) {
      val a = Imgproc.contourArea(contour)
      if (a < area * 0.2) break
      val curve = MatOfPoint2f(*contour.toArray())
      val approx = MatOfPoint2f()
      Imgproc.approxPolyDP(curve, approx, 0.02 * Imgproc.arcLength(curve, true), true)
      val points = approx.toArray()
      if (points.size == 4 && Imgproc.isContourConvex(MatOfPoint(*points))) {
        found = order(points.map { Point(it.x / scale, it.y / scale) })
      }
      curve.release(); approx.release()
      if (found != null) break
    }
    contours.forEach { it.release() }
    listOf(small, gray, edges, kernel, hierarchy).forEach { it.release() }
    return found
  }

  /** Top-left, top-right, bottom-right, bottom-left. */
  private fun order(p: List<Point>): Array<Point> {
    val tl = p.minByOrNull { it.x + it.y }!!
    val br = p.maxByOrNull { it.x + it.y }!!
    val tr = p.minByOrNull { it.y - it.x }!!
    val bl = p.maxByOrNull { it.y - it.x }!!
    return arrayOf(tl, tr, br, bl)
  }

  private fun flatten(rgb: Mat, c: Array<Point>): Mat {
    fun d(a: Point, b: Point) = hypot(a.x - b.x, a.y - b.y)
    var w = max(d(c[0], c[1]), d(c[3], c[2]))
    var h = max(d(c[0], c[3]), d(c[1], c[2]))
    // An A4 page at 300 dpi is about 3508 pixels long: more only makes a larger file.
    val shrink = min(1.0, 3508.0 / max(w, h))
    w *= shrink; h *= shrink
    val from = MatOfPoint2f(*c)
    val to = MatOfPoint2f(Point(0.0, 0.0), Point(w - 1, 0.0), Point(w - 1, h - 1), Point(0.0, h - 1))
    val transform = Imgproc.getPerspectiveTransform(from, to)
    val flat = Mat()
    Imgproc.warpPerspective(rgb, flat, transform, Size(w, h), Imgproc.INTER_CUBIC, Core.BORDER_REPLICATE)
    from.release(); to.release(); transform.release()
    return flat
  }

  /** Paper made white: each channel is divided by its own estimated background, then levels. */
  private fun clean(page: Mat, style: String): Mat {
    val source = Mat()
    if (style == "color") page.copyTo(source) else Imgproc.cvtColor(page, source, Imgproc.COLOR_RGB2GRAY)
    val long = max(page.cols(), page.rows()).toDouble()
    val k = 4.0
    val small = Mat(); val bg = Mat()
    Imgproc.resize(source, small, Size(source.cols() / k, source.rows() / k), 0.0, 0.0, Imgproc.INTER_AREA)
    val reach = ((long / k) / 100).roundToInt().coerceAtLeast(3) or 1
    Imgproc.dilate(small, small, Imgproc.getStructuringElement(Imgproc.MORPH_RECT, Size(reach.toDouble(), reach.toDouble())))
    Imgproc.medianBlur(small, small, 21)
    Imgproc.resize(small, bg, source.size(), 0.0, 0.0, Imgproc.INTER_LINEAR)
    val even = Mat()
    Core.divide(source, bg, even, 255.0)
    // Levels: faint grey paper to white, ink a little darker.
    even.convertTo(even, -1, 255.0 / 205.0, -30.0 * 255.0 / 205.0)
    small.release(); bg.release(); source.release()
    if (style != "bw") return even
    val bw = Mat()
    val block = ((long / 50).roundToInt().coerceAtLeast(15)) or 1
    Imgproc.adaptiveThreshold(even, bw, 255.0, Imgproc.ADAPTIVE_THRESH_GAUSSIAN_C, Imgproc.THRESH_BINARY, block, 10.0)
    even.release()
    return bw
  }
}
