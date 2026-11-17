package systems.vool.capture

import android.content.Context
import android.media.MediaRecorder
import android.os.Build
import java.io.File

/** Sound recordings for the private capture: AAC in an M4A file, written only inside VOOL. */
object SoundRecorder {
  private var recorder: MediaRecorder? = null
  private var file: File? = null
  private var startedAt = 0L

  @Synchronized
  fun start(context: Context) {
    if (recorder != null) return
    val target = CaptureStore.scratch(context, "m4a")
    val next = if (Build.VERSION.SDK_INT >= 31) MediaRecorder(context) else @Suppress("DEPRECATION") MediaRecorder()
    try {
      next.setAudioSource(MediaRecorder.AudioSource.MIC)
      next.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
      next.setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
      next.setAudioSamplingRate(48_000)
      next.setAudioEncodingBitRate(256_000)
      next.setAudioChannels(1)
      next.setOutputFile(target.absolutePath)
      next.prepare()
      next.start()
    } catch (error: Exception) {
      next.release()
      target.delete()
      throw error
    }
    recorder = next
    file = target
    startedAt = System.currentTimeMillis()
  }

  val recording: Boolean get() = recorder != null

  @Synchronized
  fun stop(context: Context) {
    val current = recorder ?: throw IllegalStateException("Nothing is recording.")
    val target = file!!
    recorder = null
    file = null
    try {
      current.stop()
    } catch (error: RuntimeException) {
      // stopped before any sound was written
      current.release()
      target.delete()
      throw IllegalStateException("The recording was too short.")
    }
    current.release()
    CaptureStore.keep(context, target, "audio/mp4", "sound", startedAt)
  }
}
