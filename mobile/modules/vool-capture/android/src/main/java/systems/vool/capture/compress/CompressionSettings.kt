package systems.vool.capture.compress

import android.content.Context
import org.json.JSONObject

/**
 * User choice for private-capture compression.
 *
 * Default: OFF. Captures go to the desktop as originals.
 * When ON, the default preset is BEST: best quality, best format the phone can make
 * (HEIC photos, HEVC video, Opus sound), location removed.
 * CUSTOM exposes every knob (power-user section).
 */
data class CompressionSettings(
    val enabled: Boolean = false,
    val preset: Preset = Preset.BEST,
    val custom: Tuning = Preset.BEST.tuning,
    /** Remove GPS / place data from compressed files. */
    val stripLocation: Boolean = true,
    /** Also remove camera make, model and lens. Date and exposure stay. */
    val stripCameraInfo: Boolean = false,
    /** Keep the original when compression saves less than this (percent of original size). */
    val minSavingPercent: Int = 10,
) {
    enum class Preset(val tuning: Tuning) {
        BEST(Tuning(photoFormat = "auto", photoQuality = 90, photoMaxEdge = 0,
            videoCodec = "auto", videoBitsPerPixel = 0.10, videoSourceCap = 0.60, videoMaxHeight = 0,
            audioCodec = "auto", audioKbps = 96)),
        BALANCED(Tuning(photoFormat = "auto", photoQuality = 80, photoMaxEdge = 0,
            videoCodec = "auto", videoBitsPerPixel = 0.07, videoSourceCap = 0.45, videoMaxHeight = 0,
            audioCodec = "auto", audioKbps = 64)),
        SMALL(Tuning(photoFormat = "auto", photoQuality = 70, photoMaxEdge = 2560,
            videoCodec = "auto", videoBitsPerPixel = 0.05, videoSourceCap = 0.30, videoMaxHeight = 1080,
            audioCodec = "auto", audioKbps = 32)),
        CUSTOM(BEST.tuning),
    }

    /**
     * photoFormat: auto (HEIC, else JPEG) | heic | jpeg
     * photoQuality: 1..100
     * photoMaxEdge: longest side in pixels, 0 = keep
     * videoCodec: auto (HEVC, else AVC) | hevc | avc | av1 (AV1 only if the phone has an encoder, else HEVC/AVC)
     * videoBitsPerPixel: target bits per pixel per frame for HEVC (AVC gets x1.5)
     * videoSourceCap: never above this fraction of the source bitrate
     * videoMbps: fixed target in Mbit/s, 0 = automatic (bits-per-pixel rule)
     * videoMaxHeight: 0 = keep resolution, else 2160 / 1440 / 1080 / 720 (short side)
     * audioCodec: auto (Opus, else AAC) | opus | aac
     * audioKbps: 16..256
     */
    data class Tuning(
        val photoFormat: String,
        val photoQuality: Int,
        val photoMaxEdge: Int,
        val videoCodec: String,
        val videoBitsPerPixel: Double,
        val videoSourceCap: Double,
        val videoMbps: Double = 0.0,
        val videoMaxHeight: Int,
        val audioCodec: String,
        val audioKbps: Int,
    ) {
        fun sanitized(): Tuning = copy(
            photoFormat = photoFormat.takeIf { it in PHOTO_FORMATS } ?: "auto",
            photoQuality = photoQuality.coerceIn(1, 100),
            photoMaxEdge = if (photoMaxEdge <= 0) 0 else photoMaxEdge.coerceIn(640, 16384),
            videoCodec = videoCodec.takeIf { it in VIDEO_CODECS } ?: "auto",
            videoBitsPerPixel = videoBitsPerPixel.takeIf { it.isFinite() }?.coerceIn(0.01, 0.5) ?: 0.10,
            videoSourceCap = videoSourceCap.takeIf { it.isFinite() }?.coerceIn(0.05, 1.0) ?: 0.60,
            videoMbps = videoMbps.takeIf { it.isFinite() && it > 0 }?.coerceIn(0.3, 200.0) ?: 0.0,
            videoMaxHeight = if (videoMaxHeight <= 0) 0 else videoMaxHeight.coerceIn(240, 4320),
            audioCodec = audioCodec.takeIf { it in AUDIO_CODECS } ?: "auto",
            audioKbps = audioKbps.coerceIn(16, 256),
        )

        fun toJson(): JSONObject = JSONObject()
            .put("photoFormat", photoFormat).put("photoQuality", photoQuality).put("photoMaxEdge", photoMaxEdge)
            .put("videoCodec", videoCodec).put("videoBitsPerPixel", videoBitsPerPixel)
            .put("videoSourceCap", videoSourceCap).put("videoMbps", videoMbps).put("videoMaxHeight", videoMaxHeight)
            .put("audioCodec", audioCodec).put("audioKbps", audioKbps)

        companion object {
            val PHOTO_FORMATS = setOf("auto", "heic", "jpeg")
            val VIDEO_CODECS = setOf("auto", "hevc", "avc", "av1")
            val AUDIO_CODECS = setOf("auto", "opus", "aac")

            fun fromJson(o: JSONObject?, base: Tuning): Tuning {
                if (o == null) return base
                return Tuning(
                    photoFormat = o.optString("photoFormat", base.photoFormat),
                    photoQuality = o.optInt("photoQuality", base.photoQuality),
                    photoMaxEdge = o.optInt("photoMaxEdge", base.photoMaxEdge),
                    videoCodec = o.optString("videoCodec", base.videoCodec),
                    videoBitsPerPixel = o.optDouble("videoBitsPerPixel", base.videoBitsPerPixel),
                    videoSourceCap = o.optDouble("videoSourceCap", base.videoSourceCap),
                    videoMbps = o.optDouble("videoMbps", base.videoMbps),
                    videoMaxHeight = o.optInt("videoMaxHeight", base.videoMaxHeight),
                    audioCodec = o.optString("audioCodec", base.audioCodec),
                    audioKbps = o.optInt("audioKbps", base.audioKbps),
                ).sanitized()
            }
        }
    }

    /** The knobs actually used: the preset's, or the user's own in CUSTOM. */
    fun tuning(): Tuning = if (preset == Preset.CUSTOM) custom.sanitized() else preset.tuning

    fun toJson(): JSONObject = JSONObject()
        .put("enabled", enabled)
        .put("preset", preset.name.lowercase())
        .put("custom", custom.toJson())
        .put("effective", tuning().toJson())
        .put("stripLocation", stripLocation)
        .put("stripCameraInfo", stripCameraInfo)
        .put("minSavingPercent", minSavingPercent)

    companion object {
        private const val PREFS = "vool_capture_compression"
        private const val KEY = "settings_v1"

        /** Unknown or broken input never turns compression on. */
        fun fromJson(o: JSONObject): CompressionSettings {
            val preset = runCatching { Preset.valueOf(o.optString("preset", "best").uppercase()) }
                .getOrDefault(Preset.BEST)
            return CompressionSettings(
                enabled = o.optBoolean("enabled", false),
                preset = preset,
                custom = Tuning.fromJson(o.optJSONObject("custom"), Preset.BEST.tuning),
                stripLocation = o.optBoolean("stripLocation", true),
                stripCameraInfo = o.optBoolean("stripCameraInfo", false),
                minSavingPercent = o.optInt("minSavingPercent", 10).coerceIn(0, 90),
            )
        }

        fun load(context: Context): CompressionSettings {
            val raw = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY, null)
                ?: return CompressionSettings()
            return runCatching { fromJson(JSONObject(raw)) }.getOrDefault(CompressionSettings())
        }

        fun save(context: Context, s: CompressionSettings) {
            context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                .putString(KEY, s.toJson().toString()).commit()
        }
    }
}
