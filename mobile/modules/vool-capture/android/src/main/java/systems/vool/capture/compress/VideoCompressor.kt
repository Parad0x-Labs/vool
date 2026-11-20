package systems.vool.capture.compress

import android.content.Context
import android.media.MediaExtractor
import android.media.MediaFormat
import android.media.MediaMetadataRetriever
import android.net.Uri
import android.os.Handler
import android.os.HandlerThread
import androidx.media3.common.MediaItem
import androidx.media3.common.MimeTypes
import androidx.media3.common.util.UnstableApi
import androidx.media3.effect.Presentation
import androidx.media3.transformer.Composition
import androidx.media3.transformer.DefaultEncoderFactory
import androidx.media3.transformer.EditedMediaItem
import androidx.media3.transformer.Effects
import androidx.media3.transformer.ExportException
import androidx.media3.transformer.ExportResult
import androidx.media3.transformer.Transformer
import androidx.media3.transformer.VideoEncoderSettings
import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference

/**
 * MP4 capture -> MP4 with HEVC (or AV1 / AVC) video, audio passed through untouched.
 * Uses the phone's hardware encoders through Media3 Transformer: no ffmpeg, no GPL encoders,
 * rotation and HDR handled by the platform. Bitrate follows bits-per-pixel and never exceeds a
 * fraction of the source bitrate, so a low-bitrate source is not blown up.
 */
@androidx.annotation.OptIn(markerClass = [UnstableApi::class])
internal object VideoCompressor {
    class Probe(val width: Int, val height: Int, val fps: Double, val bitrate: Long, val durationMs: Long, val mime: String?, val rotation: Int = 0) {
        /** Height as shown on screen (after the container rotation). */
        val displayHeight get() = if (rotation % 180 != 0) width else height
        val displayWidth get() = if (rotation % 180 != 0) height else width
    }
    class Output(val file: File, val codec: String, val encoder: String?, val targetBitrate: Int, val width: Int, val height: Int,
                 val durationMs: Long, val sourceBitrate: Long)
    class Skip(reason: String) : Exception(reason)

    fun probe(src: File): Probe {
        val r = MediaMetadataRetriever()
        try {
            r.setDataSource(src.absolutePath)
            val w = r.extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_WIDTH)?.toIntOrNull() ?: 0
            val h = r.extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_HEIGHT)?.toIntOrNull() ?: 0
            val dur = r.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)?.toLongOrNull() ?: 0L
            val br = r.extractMetadata(MediaMetadataRetriever.METADATA_KEY_BITRATE)?.toLongOrNull() ?: 0L
            val rot = r.extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_ROTATION)?.toIntOrNull() ?: 0
            var fps = 30.0
            var mime: String? = null
            val ex = MediaExtractor()
            try {
                ex.setDataSource(src.absolutePath)
                for (i in 0 until ex.trackCount) {
                    val f = ex.getTrackFormat(i)
                    val m = f.getString(MediaFormat.KEY_MIME) ?: continue
                    if (m.startsWith("video/")) {
                        mime = m
                        if (f.containsKey(MediaFormat.KEY_FRAME_RATE)) {
                            fps = runCatching { f.getInteger(MediaFormat.KEY_FRAME_RATE).toDouble() }
                                .recoverCatching { f.getFloat(MediaFormat.KEY_FRAME_RATE).toDouble() }.getOrDefault(30.0)
                        }
                        break
                    }
                }
            } finally { ex.release() }
            require(w > 0 && h > 0 && mime != null) { "no video track" }
            return Probe(w, h, fps.coerceIn(1.0, 240.0), br, dur, mime, ((rot % 360) + 360) % 360)
        } finally {
            r.release()
        }
    }

    /** Pick the codec the phone can actually encode, in quality-per-bit order. */
    fun pickCodec(wanted: String): String = when (wanted) {
        "av1" -> if (CodecSupport.av1) MimeTypes.VIDEO_AV1 else pickCodec("auto")
        "avc" -> MimeTypes.VIDEO_H264
        "hevc", "auto" -> if (CodecSupport.hevc) MimeTypes.VIDEO_H265 else MimeTypes.VIDEO_H264
        else -> pickCodec("auto")
    }

    /** Target bitrate in bit/s for the output size. */
    fun targetBitrate(p: Probe, t: CompressionSettings.Tuning, codecMime: String, outW: Int, outH: Int): Int {
        val efficiency = when (codecMime) {
            MimeTypes.VIDEO_H264 -> 1.5      // AVC needs ~50% more bits for the same look
            MimeTypes.VIDEO_AV1 -> 0.8
            else -> 1.0
        }
        val wanted = if (t.videoMbps > 0) t.videoMbps * 1_000_000
        else t.videoBitsPerPixel * outW * outH * p.fps * efficiency
        // Never above a fraction of the source (scaled to the output pixel count).
        val pixelRatio = (outW.toDouble() * outH) / (p.width.toDouble() * p.height)
        val cap = if (p.bitrate > 0) p.bitrate * t.videoSourceCap * pixelRatio.coerceAtMost(1.0) else Double.MAX_VALUE
        return minOf(wanted, cap).coerceIn(300_000.0, 200_000_000.0).toInt()
    }

    fun outputSize(p: Probe, maxShortSide: Int): Pair<Int, Int> {
        val short = minOf(p.width, p.height)
        if (maxShortSide <= 0 || short <= maxShortSide) return p.width to p.height
        val s = maxShortSide.toDouble() / short
        fun even(v: Double) = (v.toInt() / 2) * 2
        return even(p.width * s) to even(p.height * s)
    }

    fun compress(context: Context, src: File, t: CompressionSettings.Tuning, newFile: (String) -> File): Output {
        val p = probe(src)
        val (outW, outH) = outputSize(p, t.videoMaxHeight)
        val first = pickCodec(t.videoCodec)
        val order = buildList {
            add(first)
            if (first != MimeTypes.VIDEO_H265 && CodecSupport.hevc && t.videoCodec != "avc") add(MimeTypes.VIDEO_H265)
            if (first != MimeTypes.VIDEO_H264) add(MimeTypes.VIDEO_H264)
        }
        var last: Exception? = null
        for (codec in order) {
            val bitrate = targetBitrate(p, t, codec, outW, outH)
            val sameSize = outW == p.width && outH == p.height
            if (sameSize && p.bitrate > 0 && bitrate >= p.bitrate * 0.9 && codec == p.mime) {
                throw Skip("source already at or below target bitrate")
            }
            val out = newFile("mp4")
            try {
                val res = export(context, src, out, codec, bitrate, if (sameSize) 0 else minOf(outW, outH), p)
                return Output(out, codec, res.videoEncoderName, bitrate, outW, outH, p.durationMs, p.bitrate)
            } catch (e: Exception) {
                out.delete()
                last = e
                CompressionLog.note("video: ${codec} failed (${e.javaClass.simpleName}: ${e.message}), trying next")
            }
        }
        throw last ?: IllegalStateException("no video encoder")
    }

    private fun export(context: Context, src: File, out: File, codec: String, bitrate: Int,
                       shortSide: Int, p: Probe): ExportResult {
        val thread = HandlerThread("vool-compress-video").apply { start() }
        val handler = Handler(thread.looper)
        val done = CountDownLatch(1)
        val result = AtomicReference<ExportResult?>()
        val error = AtomicReference<Exception?>()
        val transformer = AtomicReference<Transformer?>()
        try {
            handler.post {
                try {
                    val encoders = DefaultEncoderFactory.Builder(context)
                        .setRequestedVideoEncoderSettings(VideoEncoderSettings.Builder().setBitrate(bitrate).build())
                        .setEnableFallback(true)
                        .build()
                    val tr = Transformer.Builder(context)
                        .setLooper(thread.looper)
                        .setVideoMimeType(codec)
                        .setEncoderFactory(encoders)
                        .addListener(object : Transformer.Listener {
                            override fun onCompleted(composition: Composition, exportResult: ExportResult) {
                                result.set(exportResult); done.countDown()
                            }
                            override fun onError(composition: Composition, exportResult: ExportResult,
                                                 exportException: ExportException) {
                                error.set(exportException); done.countDown()
                            }
                        })
                        .build()
                    transformer.set(tr)
                    val video = if (shortSide > 0) listOf(
                        // Presentation height is in display orientation; keep the short side at shortSide.
                        Presentation.createForHeight(if (p.displayHeight <= p.displayWidth) shortSide else
                            (p.displayHeight.toDouble() * shortSide / p.displayWidth).toInt() / 2 * 2)
                    ) else emptyList()
                    val item = EditedMediaItem.Builder(MediaItem.fromUri(Uri.fromFile(src)))
                        .setEffects(Effects(emptyList(), video))
                        .build()
                    tr.start(item, out.absolutePath)
                } catch (e: Exception) {
                    error.set(e); done.countDown()
                }
            }
            // Generous: slow phones encode 4K at ~0.3x real time.
            val limitMs = maxOf(120_000L, p.durationMs * 6)
            if (!done.await(limitMs, TimeUnit.MILLISECONDS)) {
                handler.post { transformer.get()?.cancel() }
                throw IllegalStateException("video compression timed out after ${limitMs / 1000}s")
            }
            error.get()?.let { throw it }
            val r = result.get() ?: error("no export result")
            require(out.length() > 0) { "empty video output" }
            // Sanity: output must be a playable video of about the same length.
            val q = probe(out)
            if (p.durationMs > 0 && q.durationMs > 0) {
                require(kotlin.math.abs(q.durationMs - p.durationMs) <= maxOf(1500L, p.durationMs / 50)) {
                    "output duration ${q.durationMs}ms vs source ${p.durationMs}ms"
                }
            }
            return r
        } finally {
            thread.quitSafely()
        }
    }
}
