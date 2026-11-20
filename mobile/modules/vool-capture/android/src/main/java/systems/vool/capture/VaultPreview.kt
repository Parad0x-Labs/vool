package systems.vool.capture

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.ImageDecoder
import android.media.MediaDataSource
import android.media.MediaMetadataRetriever
import android.os.Build
import android.util.Base64
import android.util.Log
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer

/**
 * Small pictures made on the phone from a Vault stream, in memory only: a photo at screen size or as a card
 * preview (HEIC included, which the computer may not open), a frame from a video, a recording's cover art.
 * Answers a JPEG as base64, or "" when there is nothing to show.
 */
object VaultPreview {
  private const val PHOTO_LIMIT = 96L * 1024 * 1024

  fun make(id: String, maxEdge: Int, kind: String): String {
    val stream = VaultStreams.get(id) ?: throw IllegalStateException("closed")
    val edge = maxEdge.coerceIn(64, 4096)
    val started = System.currentTimeMillis()
    val bitmap = when (kind) {
      "photo" -> photo(stream, edge)
      "video" -> retrieve(stream) { it.frame(edge) }
      "audio" -> retrieve(stream) { r -> r.embeddedPicture?.let { decode(it, edge) } }
      else -> null
    } ?: return "".also { Log.i("VoolCapture", "vault: no preview for a $kind (${System.currentTimeMillis() - started} ms)") }
    try {
      val out = ByteArrayOutputStream()
      var quality = if (edge <= 512) 78 else 88
      bitmap.compress(Bitmap.CompressFormat.JPEG, quality, out)
      // a card preview stays small enough for the computer to keep beside the item
      while (edge <= 512 && out.size() > 150 * 1024 && quality > 40) {
        out.reset(); quality -= 15; bitmap.compress(Bitmap.CompressFormat.JPEG, quality, out)
      }
      Log.i("VoolCapture", "vault: $kind preview ${bitmap.width}x${bitmap.height} ${out.size() / 1024} KB in ${System.currentTimeMillis() - started} ms")
      return Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP)
    } finally {
      bitmap.recycle()
    }
  }

  private fun photo(stream: VaultStreams.Stream, edge: Int): Bitmap? {
    if (stream.size > PHOTO_LIMIT) return null
    val bytes = ByteArray(stream.size.toInt())
    try {
      var at = 0L
      while (at < stream.size) {
        val n = VaultStreams.read(stream, at, bytes, at.toInt(), minOf(VaultStreams.PIECE.toLong(), stream.size - at).toInt())
        if (n <= 0) return null
        at += n
      }
      return decode(bytes, edge)
    } finally {
      bytes.fill(0)
    }
  }

  /** Decodes at about [edge] on the long side, upright, without ever holding the full-size picture. */
  private fun decode(bytes: ByteArray, edge: Int): Bitmap? {
    if (Build.VERSION.SDK_INT >= 28) {
      return try {
        ImageDecoder.decodeBitmap(ImageDecoder.createSource(ByteBuffer.wrap(bytes))) { decoder, info, _ ->
          val long = maxOf(info.size.width, info.size.height).coerceAtLeast(1)
          val scale = minOf(1.0, edge.toDouble() / long)
          decoder.setTargetSize((info.size.width * scale).toInt().coerceAtLeast(1), (info.size.height * scale).toInt().coerceAtLeast(1))
          decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE
        }
      } catch (error: Exception) {
        Log.i("VoolCapture", "vault: picture could not be read (${error.javaClass.simpleName})")
        null
      }
    }
    val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
    var sample = 1
    while (maxOf(bounds.outWidth, bounds.outHeight) / (sample * 2) >= edge) sample *= 2
    return BitmapFactory.decodeByteArray(bytes, 0, bytes.size, BitmapFactory.Options().apply { inSampleSize = sample })
  }

  private fun MediaMetadataRetriever.frame(edge: Int): Bitmap? {
    val at = 1_000_000L
    return if (Build.VERSION.SDK_INT >= 27) getScaledFrameAtTime(at, MediaMetadataRetriever.OPTION_CLOSEST_SYNC, edge, edge)
    else getFrameAtTime(at, MediaMetadataRetriever.OPTION_CLOSEST_SYNC)?.let { full ->
      val scale = minOf(1f, edge.toFloat() / maxOf(full.width, full.height))
      Bitmap.createScaledBitmap(full, (full.width * scale).toInt().coerceAtLeast(1), (full.height * scale).toInt().coerceAtLeast(1), true)
        .also { if (it !== full) full.recycle() }
    }
  }

  /** The system's reader on top of the sealed stream: it asks for the parts it needs, and only those arrive. */
  private fun <T> retrieve(stream: VaultStreams.Stream, use: (MediaMetadataRetriever) -> T?): T? {
    val retriever = MediaMetadataRetriever()
    return try {
      retriever.setDataSource(object : MediaDataSource() {
        override fun readAt(position: Long, buffer: ByteArray, offset: Int, size: Int): Int {
          if (size == 0) return 0
          if (position >= stream.size) return -1
          return VaultStreams.read(stream, position, buffer, offset, size)
        }
        override fun getSize(): Long = stream.size
        override fun close() {}
      })
      use(retriever)
    } catch (error: Exception) {
      Log.i("VoolCapture", "vault: media could not be read (${error.javaClass.simpleName})")
      null
    } finally {
      try { retriever.release() } catch (_: Exception) {}
    }
  }
}
