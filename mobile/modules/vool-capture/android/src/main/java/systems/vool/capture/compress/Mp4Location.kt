package systems.vool.capture.compress

import java.io.File
import java.io.RandomAccessFile

/**
 * Removes place data from an MP4/M4A in place without rewriting media:
 * location boxes ("©xyz" written by Android's muxer, 3GPP "loci") are renamed to "free"
 * and their payload zeroed, so every offset in the file stays valid.
 * Afterwards every user-data/metadata box is scanned for any ISO-6709 coordinate left; if one is found
 * the call fails, so the caller never claims "location removed" falsely.
 */
internal object Mp4Location {
    private val CONTAINERS = setOf("moov", "trak", "udta", "meta", "mdia", "minf", "stbl", "ilst", "edts")
    private val LOCATION = setOf("©xyz", "loci")
    private val ISO6709 = Regex("[+-]\\d{2}(\\.\\d+)?[+-]\\d{3}(\\.\\d+)?")

    /** Returns how many location boxes were blanked. Throws if a coordinate remains. */
    fun strip(file: File): Int {
        RandomAccessFile(file, "rw").use { f ->
            var blanked = 0
            val metaRanges = ArrayList<LongRange>()
            walk(f, 0, f.length()) { type, start, size, headerLen ->
                if (type == "udta" || type == "meta") metaRanges.add(start until start + size)
                if (type in LOCATION) {
                    f.seek(start + 4); f.write("free".toByteArray(Charsets.ISO_8859_1))
                    val payload = (size - headerLen).toInt()
                    if (payload > 0) { f.seek(start + headerLen); f.write(ByteArray(payload)) }
                    blanked++
                }
            }
            // Only user-data / metadata boxes are text-like; sample tables are not scanned.
            for (r in metaRanges) {
                val len = (r.last - r.first + 1).coerceAtMost(4L * 1024 * 1024).toInt()
                val buf = ByteArray(len)
                f.seek(r.first); f.readFully(buf)
                if (ISO6709.containsMatchIn(String(buf, Charsets.ISO_8859_1))) {
                    error("location metadata still present after strip")
                }
            }
            return blanked
        }
    }

    private fun walk(f: RandomAccessFile, from: Long, to: Long, visit: (String, Long, Long, Int) -> Unit) {
        var pos = from
        val hdr = ByteArray(16)
        while (pos + 8 <= to) {
            f.seek(pos); f.readFully(hdr, 0, 8)
            var size = u32(hdr, 0)
            val type = String(hdr, 4, 4, Charsets.ISO_8859_1)
            var headerLen = 8
            if (size == 1L) {
                f.readFully(hdr, 8, 8); size = u64(hdr, 8); headerLen = 16
            } else if (size == 0L) {
                size = to - pos
            }
            if (size < headerLen || pos + size > to) return   // malformed: stop, don't guess
            visit(type, pos, size, headerLen)
            if (type in CONTAINERS) {
                var childStart = pos + headerLen
                if (type == "meta") {
                    // ISO 'meta' is a full box (4 bytes version/flags); QuickTime 'meta' is not.
                    f.seek(childStart); f.readFully(hdr, 0, 8)
                    if (u32(hdr, 0) == 0L) childStart += 4
                }
                walk(f, childStart, pos + size, visit)
            }
            pos += size
        }
    }

    private fun u32(b: ByteArray, o: Int): Long =
        ((b[o].toLong() and 0xFF) shl 24) or ((b[o + 1].toLong() and 0xFF) shl 16) or
            ((b[o + 2].toLong() and 0xFF) shl 8) or (b[o + 3].toLong() and 0xFF)

    private fun u64(b: ByteArray, o: Int): Long = (u32(b, o) shl 32) or u32(b, o + 4)
}
