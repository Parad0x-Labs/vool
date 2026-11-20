package systems.vool.capture.compress

import android.media.MediaCodecInfo
import android.media.MediaCodecList
import android.media.MediaFormat
import android.os.Build
import org.json.JSONObject

/** What this phone can encode. Read once; MediaCodecList does not change at runtime. */
object CodecSupport {
    data class Encoder(val name: String, val hardware: Boolean)

    private val encoders: Map<String, List<Encoder>> by lazy {
        val out = HashMap<String, MutableList<Encoder>>()
        val infos = runCatching { MediaCodecList(MediaCodecList.REGULAR_CODECS).codecInfos }.getOrDefault(emptyArray())
        for (info in infos) {
            if (!info.isEncoder) continue
            for (type in info.supportedTypes) {
                out.getOrPut(type.lowercase()) { mutableListOf() }.add(Encoder(info.name, isHardware(info)))
            }
        }
        out
    }

    private fun isHardware(info: MediaCodecInfo): Boolean {
        if (Build.VERSION.SDK_INT >= 29) return info.isHardwareAccelerated
        val n = info.name.lowercase()
        return !(n.startsWith("omx.google.") || n.startsWith("c2.android.") || n.contains(".sw."))
    }

    fun has(mime: String) = encoders[mime.lowercase()].orEmpty().isNotEmpty()
    fun hasHardware(mime: String) = encoders[mime.lowercase()].orEmpty().any { it.hardware }

    val hevc get() = has(MediaFormat.MIMETYPE_VIDEO_HEVC)
    val avc get() = has(MediaFormat.MIMETYPE_VIDEO_AVC)
    /** AV1 only counts with a hardware encoder; software AV1 on a phone is far too slow. */
    val av1 get() = Build.VERSION.SDK_INT >= 29 && hasHardware("video/av01")
    /** HEIC needs HeifWriter (API 28) and an HEVC encoder. */
    val heic get() = Build.VERSION.SDK_INT >= 28 && hevc
    /** Opus encoding plus the Ogg muxer arrived in API 29. */
    val opus get() = Build.VERSION.SDK_INT >= 29 && has(MediaFormat.MIMETYPE_AUDIO_OPUS)
    val aac get() = has(MediaFormat.MIMETYPE_AUDIO_AAC)

    fun opusSupportsRate(rate: Int): Boolean = supportsRate(MediaFormat.MIMETYPE_AUDIO_OPUS, rate)
    fun aacSupportsRate(rate: Int): Boolean = supportsRate(MediaFormat.MIMETYPE_AUDIO_AAC, rate)

    private fun supportsRate(mime: String, rate: Int): Boolean {
        val infos = runCatching { MediaCodecList(MediaCodecList.REGULAR_CODECS).codecInfos }.getOrDefault(emptyArray())
        return infos.any { info ->
            info.isEncoder && info.supportedTypes.any { it.equals(mime, true) } &&
                runCatching { info.getCapabilitiesForType(mime).audioCapabilities?.isSampleRateSupported(rate) == true }
                    .getOrDefault(false)
        }
    }

    fun toJson(): JSONObject = JSONObject()
        .put("sdk", Build.VERSION.SDK_INT)
        .put("heic", heic).put("hevc", hevc).put("hevcHardware", hasHardware(MediaFormat.MIMETYPE_VIDEO_HEVC))
        .put("avc", avc).put("av1", av1).put("opus", opus).put("aac", aac)
}
