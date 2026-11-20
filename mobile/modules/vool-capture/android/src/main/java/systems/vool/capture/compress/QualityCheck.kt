package systems.vool.capture.compress

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.media.MediaMetadataRetriever
import java.io.File
import kotlin.math.log10
import kotlin.math.min

/**
 * Measures how close the compressed file looks to the original before the original is let go.
 * PSNR on brightness (Y), at a reduced size so it is fast and a planned downscale is not punished.
 * Below [MIN_PSNR_DB] the original is kept. A compressed file that cannot be decoded is a failure.
 */
internal object QualityCheck {
    const val MIN_PSNR_DB = 30.0
    private const val SIDE = 1024

    fun photo(original: File, compressed: File): Double {
        val a = decodeSmall(original) ?: error("original does not decode")
        val b = decodeSmall(compressed) ?: run { a.recycle(); error("compressed photo does not decode") }
        try {
            // The compressed photo may come back already turned (HEIF irot). Compare in every orientation
            // whose shape matches and keep the best: orientation is checked elsewhere, this checks pixels.
            var best = Double.NEGATIVE_INFINITY
            for (deg in intArrayOf(0, 90, 180, 270)) {
                val r = rotate(a, deg)
                val ar = r.width.toDouble() / r.height; val br = b.width.toDouble() / b.height
                if (kotlin.math.abs(ar - br) < 0.02) best = maxOf(best, psnr(r, b))
                if (r !== a) r.recycle()
            }
            check(best.isFinite()) { "compressed photo has a different shape" }
            return best
        } finally { a.recycle(); b.recycle() }
    }

    /** Mean PSNR over frames at 10%, 50% and 90% of the clip. */
    fun video(original: File, compressed: File, durationMs: Long): Double {
        val times = if (durationMs > 0) listOf(0.1, 0.5, 0.9).map { (durationMs * it * 1000).toLong() } else listOf(0L)
        val ra = MediaMetadataRetriever(); val rb = MediaMetadataRetriever()
        try {
            ra.setDataSource(original.absolutePath); rb.setDataSource(compressed.absolutePath)
            val scores = times.map { us ->
                val fa = ra.getFrameAtTime(us, MediaMetadataRetriever.OPTION_CLOSEST) ?: error("original frame missing")
                val fb = rb.getFrameAtTime(us, MediaMetadataRetriever.OPTION_CLOSEST)
                    ?: run { fa.recycle(); error("compressed video frame does not decode") }
                try {
                    val sa = fit(fa); val sb = Bitmap.createScaledBitmap(fb, sa.width, sa.height, true)
                    try { psnr(sa, sb) } finally { if (sa !== fa) sa.recycle(); if (sb !== fb) sb.recycle() }
                } finally { fa.recycle(); fb.recycle() }
            }
            return scores.average()
        } finally { ra.release(); rb.release() }
    }

    private fun decodeSmall(f: File): Bitmap? {
        val o = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(f.absolutePath, o)
        if (o.outWidth <= 0) return null
        var s = 1
        while (maxOf(o.outWidth, o.outHeight) / (s * 2) >= SIDE) s *= 2
        val bmp = BitmapFactory.decodeFile(f.absolutePath, BitmapFactory.Options().apply { inSampleSize = s }) ?: return null
        val fitted = fit(bmp)
        if (fitted !== bmp) bmp.recycle()
        return fitted
    }

    private fun fit(b: Bitmap): Bitmap {
        val edge = maxOf(b.width, b.height)
        if (edge <= SIDE) return b
        val k = SIDE.toDouble() / edge
        return Bitmap.createScaledBitmap(b, (b.width * k).toInt().coerceAtLeast(1), (b.height * k).toInt().coerceAtLeast(1), true)
    }

    private fun rotate(b: Bitmap, deg: Int): Bitmap =
        if (deg == 0) b else Bitmap.createBitmap(b, 0, 0, b.width, b.height, Matrix().apply { postRotate(deg.toFloat()) }, true)

    private fun psnr(a: Bitmap, bIn: Bitmap): Double {
        val b = if (a.width == bIn.width && a.height == bIn.height) bIn else Bitmap.createScaledBitmap(bIn, a.width, a.height, true)
        try {
            val w = a.width; val h = a.height
            val pa = IntArray(w); val pb = IntArray(w)
            var sum = 0.0
            for (y in 0 until h) {
                a.getPixels(pa, 0, w, 0, y, w, 1); b.getPixels(pb, 0, w, 0, y, w, 1)
                for (x in 0 until w) { val d = luma(pa[x]) - luma(pb[x]); sum += d * d }
            }
            val mse = sum / (w.toDouble() * h)
            return if (mse <= 1e-10) 99.0 else min(99.0, 10 * log10(255.0 * 255.0 / mse))
        } finally { if (b !== bIn) b.recycle() }
    }

    private fun luma(p: Int): Double =
        0.299 * ((p shr 16) and 0xFF) + 0.587 * ((p shr 8) and 0xFF) + 0.114 * (p and 0xFF)
}
