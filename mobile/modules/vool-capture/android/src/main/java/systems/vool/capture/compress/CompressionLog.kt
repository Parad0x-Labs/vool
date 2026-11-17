package systems.vool.capture.compress

import android.content.Context
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * Receipts for every capture that went through compression: sizes, codec, time, outcome.
 * The settings screen shows them ("saved 1.2 GB") and they are the evidence for tests.
 * Holds no file names, paths or content.
 */
object CompressionLog {
    private const val PREFS = "vool_capture_compression"
    private const val KEY = "receipts_v1"
    private const val MAX = 200
    private const val TAG = "VoolCompress"

    @Volatile private var ctx: Context? = null
    @Volatile var scratchDir: File? = null; private set
    private val notes = ThreadLocal<MutableList<String>>()

    fun init(context: Context) {
        ctx = context.applicationContext
        val dir = File(context.noBackupFilesDir, "compress-tmp")
        dir.mkdirs()
        // Leftovers from a killed run hold plain capture data: remove them.
        dir.listFiles()?.forEach { it.delete() }
        scratchDir = dir
    }

    internal fun begin() { notes.set(mutableListOf()) }

    internal fun note(msg: String) {
        Log.i(TAG, msg)
        notes.get()?.add(msg)
    }

    internal fun record(r: JSONObject) {
        notes.get()?.let { if (it.isNotEmpty()) r.put("notes", JSONArray(it)) }
        notes.remove()
        Log.i(TAG, r.toString())
        val c = ctx ?: return
        synchronized(this) {
            val p = c.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            val arr = runCatching { JSONArray(p.getString(KEY, "[]")) }.getOrDefault(JSONArray())
            arr.put(r)
            val trimmed = JSONArray()
            for (i in maxOf(0, arr.length() - MAX) until arr.length()) trimmed.put(arr.get(i))
            p.edit().putString(KEY, trimmed.toString()).apply()
        }
    }

    fun receipts(context: Context): JSONArray =
        runCatching {
            JSONArray(context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY, "[]"))
        }.getOrDefault(JSONArray())

    /** Totals for the settings screen. */
    fun summary(context: Context): JSONObject {
        val arr = receipts(context)
        var inB = 0L; var outB = 0L; var compressed = 0; var kept = 0; var failed = 0
        for (i in 0 until arr.length()) {
            val o = arr.optJSONObject(i) ?: continue
            inB += o.optLong("inBytes"); outB += o.optLong("outBytes")
            when (o.optString("outcome")) {
                "compressed" -> compressed++
                "failed" -> failed++
                else -> kept++
            }
        }
        return JSONObject().put("items", arr.length()).put("compressed", compressed).put("keptOriginal", kept)
            .put("failed", failed).put("inBytes", inB).put("outBytes", outB).put("savedBytes", inB - outB)
    }
}
