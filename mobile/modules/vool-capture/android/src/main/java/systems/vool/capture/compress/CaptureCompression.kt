package systems.vool.capture.compress

import android.content.Context
import android.os.SystemClock
import androidx.media3.common.util.UnstableApi
import org.json.JSONObject
import systems.vool.capture.CaptureStore
import java.io.File

/**
 * Optional compression for private captures, run by the capture store on the plain scratch
 * capture (app-private, no backup) just before it is sealed. Compressed or not, what is stored
 * is sealed and hash-checked by the desktop the same way.
 *
 * Contract with CaptureStore.beforeStore:
 *  - returns the same File when nothing changes (OFF, unknown type, no gain, any failure);
 *  - returns a new scratch File only after it has been written, re-read and checked;
 *  - never deletes or edits the original; the store deletes it once the new file is kept;
 *  - every partial output is deleted here, on success and on failure.
 */
@androidx.annotation.OptIn(markerClass = [UnstableApi::class])
object CaptureCompression {
    private val oneAtATime = Any()   // hardware encoders are shared; never run two jobs together

    fun install(context: Context) {
        val app = context.applicationContext
        CompressionLog.init(app)
        CaptureStore.beforeStore = { file, mime -> process(app, file, mime) }
    }

    fun process(context: Context, src: File, mime: String): File {
        val settings = runCatching { CompressionSettings.load(context) }.getOrDefault(CompressionSettings())
        if (!settings.enabled) return src
        val kind = when {
            mime.startsWith("image/") -> "photo"
            mime.startsWith("video/") -> "video"
            mime.startsWith("audio/") -> "audio"
            else -> return src
        }
        synchronized(oneAtATime) { return run(context, src, mime, kind, settings) }
    }

    private fun run(context: Context, src: File, mime: String, kind: String, s: CompressionSettings): File {
        val made = mutableListOf<File>()
        val newFile: (String) -> File = { ending -> CaptureStore.work(context, ending).also { made += it } }
        val t = s.tuning()
        val started = SystemClock.elapsedRealtime()
        val inBytes = src.length()
        val receipt = JSONObject().put("at", System.currentTimeMillis()).put("kind", kind).put("inMime", mime)
            .put("preset", s.preset.name.lowercase()).put("inBytes", inBytes)
        CompressionLog.begin()
        var keep: File? = null
        try {
            if (inBytes <= 0) return src.also { receipt.put("outcome", "kept_original").put("reason", "empty input") }
            val out: File = try {
                when (kind) {
                    "photo" -> {
                        val r = PhotoCompressor.compress(src, t, s, newFile)
                        receipt.put("codec", r.format).put("width", r.width).put("height", r.height)
                        receipt.put("psnrDb", round1(QualityCheck.photo(src, r.file)))
                        r.file
                    }
                    "video" -> {
                        val r = VideoCompressor.compress(context, src, t, newFile)
                        receipt.put("codec", r.codec).put("encoder", r.encoder).put("targetBitrate", r.targetBitrate)
                            .put("sourceBitrate", r.sourceBitrate).put("width", r.width).put("height", r.height)
                            .put("durationMs", r.durationMs)
                        receipt.put("psnrDb", round1(QualityCheck.video(src, r.file, r.durationMs)))
                        if (s.stripLocation) receipt.put("locationBoxesRemoved", Mp4Location.strip(r.file))
                        r.file
                    }
                    else -> {
                        val r = AudioCompressor.compress(src, t, newFile)
                        receipt.put("codec", r.codec).put("kbps", r.kbps)
                        if (s.stripLocation && r.codec == "aac") Mp4Location.strip(r.file)
                        r.file
                    }
                }
            } catch (e: VideoCompressor.Skip) {
                return keepOriginal(src, kind, s, receipt, e.message, newFile).also { keep = it }
            } catch (e: AudioCompressor.Skip) {
                return keepOriginal(src, kind, s, receipt, e.message, newFile).also { keep = it }
            }

            val outBytes = out.length()
            require(out.isFile && outBytes > 0) { "no output" }
            val psnr = receipt.optDouble("psnrDb", Double.NaN)
            if (!psnr.isNaN() && psnr < QualityCheck.MIN_PSNR_DB) {
                receipt.put("triedBytes", outBytes)
                return keepOriginal(src, kind, s, receipt,
                    "quality check: ${psnr} dB < ${QualityCheck.MIN_PSNR_DB} dB", newFile).also { keep = it }
            }
            val limit = inBytes * (100 - s.minSavingPercent) / 100
            if (outBytes > limit) {
                receipt.put("triedBytes", outBytes)
                return keepOriginal(src, kind, s, receipt,
                    "saved only ${100 - outBytes * 100 / inBytes}% (< ${s.minSavingPercent}%)", newFile).also { keep = it }
            }
            receipt.put("outcome", "compressed").put("outBytes", outBytes).put("ending", out.extension)
                .put("locationRemoved", s.stripLocation)
            keep = out
            return out
        } catch (e: Throwable) {   // incl. OutOfMemoryError on huge photos: the original always survives
            receipt.put("outcome", "failed").put("reason", "${e.javaClass.simpleName}: ${e.message}")
                .put("outBytes", inBytes)
            keep = null
            return src
        } finally {
            for (f in made) if (f != keep) f.delete()
            receipt.put("ms", SystemClock.elapsedRealtime() - started)
            if (!receipt.has("outBytes")) receipt.put("outBytes", (keep ?: src).length())
            CompressionLog.record(receipt)
        }
    }

    private fun round1(v: Double) = Math.round(v * 10) / 10.0

    /**
     * No worthwhile saving: send the original. If the user wants location removed, send a copy
     * with only the place data taken out (pixels and samples untouched); if that fails, the
     * untouched original goes (it only ever travels to the user's own paired desktop).
     */
    private fun keepOriginal(src: File, kind: String, s: CompressionSettings, receipt: JSONObject,
                             reason: String?, newFile: (String) -> File): File {
        receipt.put("outcome", "kept_original").put("reason", reason ?: "no gain")
        if (!s.stripLocation) return src
        return try {
            when (kind) {
                "photo" -> newFile("jpg").also { PhotoCompressor.stripLocationCopy(src, it) }
                "video" -> newFile("mp4").also { src.copyTo(it, overwrite = true); Mp4Location.strip(it) }
                else -> if (src.extension.lowercase() in setOf("m4a", "mp4", "aac"))
                    newFile("m4a").also { src.copyTo(it, overwrite = true); Mp4Location.strip(it) } else src
            }.also { if (it != src) { receipt.put("locationRemoved", true).put("outBytes", it.length()) } }
        } catch (e: Exception) {
            receipt.put("locationRemoved", false).put("locationError", "${e.javaClass.simpleName}: ${e.message}")
            src
        }
    }
}
