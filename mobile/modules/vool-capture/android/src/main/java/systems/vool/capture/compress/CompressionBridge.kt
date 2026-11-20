package systems.vool.capture.compress

import android.content.Context
import org.json.JSONObject

/** Thin JSON calls for the settings screen (passed through by VoolCaptureModule). */
object CompressionBridge {
    fun getJson(context: Context): String = CompressionSettings.load(context).toJson()
        .put("capabilities", CodecSupport.toJson())
        .put("summary", CompressionLog.summary(context))
        .toString()

    /** Saves and returns the stored state. Anything unreadable leaves the old settings as they were. */
    fun setJson(context: Context, json: String): String {
        val next = CompressionSettings.fromJson(JSONObject(json))
        CompressionSettings.save(context, next)
        return getJson(context)
    }

    fun capabilitiesJson(): String = CodecSupport.toJson().toString()

    fun receiptsJson(context: Context): String = CompressionLog.receipts(context).toString()
}
