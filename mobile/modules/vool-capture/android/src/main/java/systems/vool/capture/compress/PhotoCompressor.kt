package systems.vool.capture.compress

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import androidx.exifinterface.media.ExifInterface
import androidx.heifwriter.HeifWriter
import java.io.File
import java.io.FileOutputStream

/**
 * JPEG capture -> HEIC (hardware HEVC, HeifWriter) or JPEG.
 * Orientation is kept without re-rotating pixels. Metadata is rebuilt from a whitelist,
 * so serial numbers, owner names and maker notes never travel; GPS only if the user allows it.
 */
internal object PhotoCompressor {
    class Output(val file: File, val format: String, val width: Int, val height: Int)

    fun compress(src: File, tuning: CompressionSettings.Tuning, settings: CompressionSettings,
                 newFile: (String) -> File): Output {
        val srcExif = runCatching { ExifInterface(src.absolutePath) }.getOrNull()
        val bitmap = decode(src, tuning.photoMaxEdge)
        try {
            val wantHeic = tuning.photoFormat == "heic" || tuning.photoFormat == "auto"
            if (wantHeic && CodecSupport.heic) {
                val out = newFile("heic")
                try {
                    writeHeic(bitmap, out, tuning.photoQuality, rotationOf(srcExif), srcExif, settings)
                    return Output(out, "heic", bitmap.width, bitmap.height)
                } catch (e: Exception) {
                    out.delete()
                    if (tuning.photoFormat == "heic") throw e   // user asked for HEIC only
                    CompressionLog.note("photo: HEIC failed (${e.javaClass.simpleName}: ${e.message}), using JPEG")
                }
            }
            val out = newFile("jpg")
            try {
                writeJpeg(bitmap, out, tuning.photoQuality, srcExif, settings)
            } catch (e: Exception) {
                out.delete(); throw e
            }
            return Output(out, "jpeg", bitmap.width, bitmap.height)
        } finally {
            bitmap.recycle()
        }
    }

    private fun decode(src: File, maxEdge: Int): Bitmap {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(src.absolutePath, bounds)
        require(bounds.outWidth > 0 && bounds.outHeight > 0) { "not a decodable image" }
        val longEdge = maxOf(bounds.outWidth, bounds.outHeight)
        var sample = 1
        if (maxEdge in 1 until longEdge) while (longEdge / (sample * 2) >= maxEdge) sample *= 2
        val opts = BitmapFactory.Options().apply {
            inSampleSize = sample
            inPreferredConfig = Bitmap.Config.ARGB_8888
        }
        var bmp = BitmapFactory.decodeFile(src.absolutePath, opts) ?: error("image decode failed")
        val edge = maxOf(bmp.width, bmp.height)
        if (maxEdge in 1 until edge) {
            val scale = maxEdge.toDouble() / edge
            val scaled = Bitmap.createScaledBitmap(bmp, (bmp.width * scale).toInt().coerceAtLeast(1),
                (bmp.height * scale).toInt().coerceAtLeast(1), true)
            if (scaled !== bmp) bmp.recycle()
            bmp = scaled
        }
        return bmp
    }

    private fun rotationOf(exif: ExifInterface?): Int = when (exif?.getAttributeInt(
        ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL)) {
        ExifInterface.ORIENTATION_ROTATE_90 -> 90
        ExifInterface.ORIENTATION_ROTATE_180 -> 180
        ExifInterface.ORIENTATION_ROTATE_270 -> 270
        else -> 0
    }

    private fun writeHeic(bmp: Bitmap, out: File, quality: Int, rotation: Int,
                          srcExif: ExifInterface?, settings: CompressionSettings) {
        val writer = HeifWriter.Builder(out.absolutePath, bmp.width, bmp.height, HeifWriter.INPUT_MODE_BITMAP)
            .setQuality(quality)
            .setRotation(rotation)       // HEIF 'irot': pixels untouched, viewers rotate
            .setGridEnabled(true)        // tiles: works for any resolution the HEVC encoder can't do in one frame
            .setMaxImages(1)
            .build()
        try {
            writer.start()
            writer.addBitmap(bmp)
            // Orientation travels as irot; EXIF says "normal" so no viewer rotates twice.
            exifBlock(srcExif, settings, orientationNormal = true)?.let { writer.addExifData(0, it, 0, it.size) }
            writer.stop(60_000)
        } finally {
            writer.close()
        }
        require(out.length() > 0) { "HEIC writer produced nothing" }
    }

    private fun writeJpeg(bmp: Bitmap, out: File, quality: Int, srcExif: ExifInterface?, settings: CompressionSettings) {
        FileOutputStream(out).use { fos ->
            check(bmp.compress(Bitmap.CompressFormat.JPEG, quality, fos)) { "JPEG encode failed" }
            fos.fd.sync()
        }
        if (srcExif != null) {
            val dst = ExifInterface(out.absolutePath)
            copyAllowed(srcExif, dst, settings, orientationNormal = false)
            dst.saveAttributes()
        }
        // Decode check: a broken file must never replace the original.
        val b = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(out.absolutePath, b)
        require(b.outWidth == bmp.width && b.outHeight == bmp.height) { "JPEG output does not decode" }
    }

    /** "Exif\0\0" + TIFF, as HeifWriter.addExifData wants, built through a throwaway 1x1 JPEG. */
    private fun exifBlock(srcExif: ExifInterface?, settings: CompressionSettings, orientationNormal: Boolean): ByteArray? {
        if (srcExif == null) return null
        val tmp = File.createTempFile("exif", ".jpg", CompressionLog.scratchDir ?: return null)
        try {
            val px = Bitmap.createBitmap(1, 1, Bitmap.Config.ARGB_8888)
            FileOutputStream(tmp).use { px.compress(Bitmap.CompressFormat.JPEG, 50, it) }
            px.recycle()
            val e = ExifInterface(tmp.absolutePath)
            copyAllowed(srcExif, e, settings, orientationNormal)
            e.saveAttributes()
            return extractApp1(tmp.readBytes())
        } finally {
            tmp.delete()
        }
    }

    /** Find the APP1 "Exif\0\0" segment payload in a JPEG. */
    internal fun extractApp1(jpeg: ByteArray): ByteArray? {
        var i = 2
        while (i + 4 <= jpeg.size && jpeg[i] == 0xFF.toByte()) {
            val marker = jpeg[i + 1].toInt() and 0xFF
            if (marker == 0xDA || marker == 0xD9) return null
            val len = ((jpeg[i + 2].toInt() and 0xFF) shl 8) or (jpeg[i + 3].toInt() and 0xFF)
            if (len < 2 || i + 2 + len > jpeg.size) return null
            if (marker == 0xE1 && len >= 8 &&
                jpeg[i + 4] == 'E'.code.toByte() && jpeg[i + 5] == 'x'.code.toByte() &&
                jpeg[i + 6] == 'i'.code.toByte() && jpeg[i + 7] == 'f'.code.toByte() &&
                jpeg[i + 8] == 0.toByte() && jpeg[i + 9] == 0.toByte()) {
                return jpeg.copyOfRange(i + 4, i + 2 + len)
            }
            i += 2 + len
        }
        return null
    }

    private val BASE_TAGS = listOf(
        ExifInterface.TAG_DATETIME, ExifInterface.TAG_DATETIME_ORIGINAL, ExifInterface.TAG_DATETIME_DIGITIZED,
        ExifInterface.TAG_OFFSET_TIME, ExifInterface.TAG_OFFSET_TIME_ORIGINAL, ExifInterface.TAG_OFFSET_TIME_DIGITIZED,
        ExifInterface.TAG_SUBSEC_TIME, ExifInterface.TAG_SUBSEC_TIME_ORIGINAL, ExifInterface.TAG_SUBSEC_TIME_DIGITIZED,
        ExifInterface.TAG_F_NUMBER, ExifInterface.TAG_EXPOSURE_TIME, ExifInterface.TAG_PHOTOGRAPHIC_SENSITIVITY,
        ExifInterface.TAG_FOCAL_LENGTH, ExifInterface.TAG_FOCAL_LENGTH_IN_35MM_FILM, ExifInterface.TAG_FLASH,
        ExifInterface.TAG_WHITE_BALANCE, ExifInterface.TAG_EXPOSURE_BIAS_VALUE, ExifInterface.TAG_APERTURE_VALUE,
        ExifInterface.TAG_SHUTTER_SPEED_VALUE, ExifInterface.TAG_METERING_MODE, ExifInterface.TAG_EXPOSURE_PROGRAM,
        ExifInterface.TAG_EXPOSURE_MODE, ExifInterface.TAG_SCENE_CAPTURE_TYPE, ExifInterface.TAG_COLOR_SPACE,
        ExifInterface.TAG_DIGITAL_ZOOM_RATIO,
    )
    private val CAMERA_TAGS = listOf(
        ExifInterface.TAG_MAKE, ExifInterface.TAG_MODEL, ExifInterface.TAG_LENS_MAKE, ExifInterface.TAG_LENS_MODEL,
    )
    private val GPS_TAGS = listOf(
        ExifInterface.TAG_GPS_LATITUDE, ExifInterface.TAG_GPS_LATITUDE_REF, ExifInterface.TAG_GPS_LONGITUDE,
        ExifInterface.TAG_GPS_LONGITUDE_REF, ExifInterface.TAG_GPS_ALTITUDE, ExifInterface.TAG_GPS_ALTITUDE_REF,
        ExifInterface.TAG_GPS_TIMESTAMP, ExifInterface.TAG_GPS_DATESTAMP, ExifInterface.TAG_GPS_PROCESSING_METHOD,
        ExifInterface.TAG_GPS_IMG_DIRECTION, ExifInterface.TAG_GPS_IMG_DIRECTION_REF,
        ExifInterface.TAG_GPS_SPEED, ExifInterface.TAG_GPS_SPEED_REF,
    )

    /** Every GPS tag ExifInterface knows, for removal. */
    private val ALL_GPS_TAGS = GPS_TAGS + listOf(
        ExifInterface.TAG_GPS_VERSION_ID, ExifInterface.TAG_GPS_SATELLITES, ExifInterface.TAG_GPS_STATUS,
        ExifInterface.TAG_GPS_MEASURE_MODE, ExifInterface.TAG_GPS_DOP, ExifInterface.TAG_GPS_TRACK_REF,
        ExifInterface.TAG_GPS_TRACK, ExifInterface.TAG_GPS_MAP_DATUM, ExifInterface.TAG_GPS_DEST_LATITUDE_REF,
        ExifInterface.TAG_GPS_DEST_LATITUDE, ExifInterface.TAG_GPS_DEST_LONGITUDE_REF,
        ExifInterface.TAG_GPS_DEST_LONGITUDE, ExifInterface.TAG_GPS_DEST_BEARING_REF,
        ExifInterface.TAG_GPS_DEST_BEARING, ExifInterface.TAG_GPS_DEST_DISTANCE_REF,
        ExifInterface.TAG_GPS_DEST_DISTANCE, ExifInterface.TAG_GPS_AREA_INFORMATION,
        ExifInterface.TAG_GPS_DIFFERENTIAL, ExifInterface.TAG_GPS_H_POSITIONING_ERROR,
    )

    internal fun copyAllowed(src: ExifInterface, dst: ExifInterface, s: CompressionSettings, orientationNormal: Boolean) {
        val tags = BASE_TAGS + (if (s.stripCameraInfo) emptyList() else CAMERA_TAGS) +
            (if (s.stripLocation) emptyList() else GPS_TAGS)
        for (t in tags) src.getAttribute(t)?.let { dst.setAttribute(t, it) }
        dst.setAttribute(ExifInterface.TAG_ORIENTATION,
            if (orientationNormal) ExifInterface.ORIENTATION_NORMAL.toString()
            else (src.getAttribute(ExifInterface.TAG_ORIENTATION) ?: ExifInterface.ORIENTATION_NORMAL.toString()))
        dst.setAttribute(ExifInterface.TAG_SOFTWARE, null)
    }

    /**
     * Location-only removal on a JPEG we are keeping as original (compression did not pay off).
     * Pixels untouched: ExifInterface rewrites only the APP1 segment.
     */
    fun stripLocationCopy(src: File, out: File) {
        src.copyTo(out, overwrite = true)
        val e = ExifInterface(out.absolutePath)
        for (t in ALL_GPS_TAGS) e.setAttribute(t, null)
        e.saveAttributes()
    }
}
