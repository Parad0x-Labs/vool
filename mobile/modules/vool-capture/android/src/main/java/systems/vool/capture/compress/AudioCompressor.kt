package systems.vool.capture.compress

import android.media.MediaCodec
import android.media.MediaExtractor
import android.media.MediaFormat
import android.media.MediaMuxer
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * Sound recording (AAC in MP4) -> Opus in Ogg (best quality per bit, API 29+),
 * else AAC-LC in M4A at the chosen bitrate. Streams buffer by buffer: a long recording is
 * never held in memory.
 */
internal object AudioCompressor {
    class Output(val file: File, val codec: String, val kbps: Int, val sampleRate: Int, val channels: Int)
    class Skip(reason: String) : Exception(reason)

    private const val TIMEOUT_US = 10_000L

    fun compress(src: File, t: CompressionSettings.Tuning, newFile: (String) -> File): Output {
        val ex = MediaExtractor()
        try {
            ex.setDataSource(src.absolutePath)
            val track = (0 until ex.trackCount).firstOrNull {
                ex.getTrackFormat(it).getString(MediaFormat.KEY_MIME)?.startsWith("audio/") == true
            } ?: error("no audio track")
            val inFmt = ex.getTrackFormat(track)
            val rate = inFmt.getInteger(MediaFormat.KEY_SAMPLE_RATE)
            val channels = inFmt.getInteger(MediaFormat.KEY_CHANNEL_COUNT)
            val srcKbps = if (inFmt.containsKey(MediaFormat.KEY_BIT_RATE)) inFmt.getInteger(MediaFormat.KEY_BIT_RATE) / 1000
            else (src.length() * 8 / 1000 / maxOf(1L, durationSec(inFmt))).toInt()

            val useOpus = (t.audioCodec == "opus" || t.audioCodec == "auto") && CodecSupport.opus &&
                CodecSupport.opusSupportsRate(rate) && channels <= 2
            if (t.audioCodec == "opus" && !useOpus) {
                CompressionLog.note("audio: Opus not possible here (rate $rate, ch $channels), using AAC")
            }
            val kbps = t.audioKbps
            if (!useOpus && srcKbps in 1..(kbps + kbps / 10)) throw Skip("source AAC already ${srcKbps}kbps")

            val (mime, ending, muxFmt) = if (useOpus)
                Triple(MediaFormat.MIMETYPE_AUDIO_OPUS, "ogg", MediaMuxer.OutputFormat.MUXER_OUTPUT_OGG)
            else Triple(MediaFormat.MIMETYPE_AUDIO_AAC, "m4a", MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4)

            val out = newFile(ending)
            try {
                transcode(ex, track, inFmt, mime, rate, channels, kbps * 1000, out, muxFmt)
            } catch (e: Exception) {
                out.delete(); throw e
            }
            require(out.length() > 0) { "empty audio output" }
            return Output(out, if (useOpus) "opus" else "aac", kbps, rate, channels)
        } finally {
            ex.release()
        }
    }

    private fun durationSec(f: MediaFormat): Long =
        if (f.containsKey(MediaFormat.KEY_DURATION)) f.getLong(MediaFormat.KEY_DURATION) / 1_000_000 else 1

    private fun transcode(ex: MediaExtractor, track: Int, inFmt: MediaFormat, outMime: String,
                          rate: Int, channels: Int, bitrate: Int, out: File, muxFmt: Int) {
        ex.selectTrack(track)
        val decoder = MediaCodec.createDecoderByType(inFmt.getString(MediaFormat.KEY_MIME)!!)
        val encFmt = MediaFormat.createAudioFormat(outMime, rate, channels).apply {
            setInteger(MediaFormat.KEY_BIT_RATE, bitrate)
            setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, 64 * 1024)
            if (outMime == MediaFormat.MIMETYPE_AUDIO_AAC) {
                setInteger(MediaFormat.KEY_AAC_PROFILE, android.media.MediaCodecInfo.CodecProfileLevel.AACObjectLC)
            }
        }
        val encoder = MediaCodec.createEncoderByType(outMime)
        var muxer: MediaMuxer? = null
        var muxStarted = false
        try {
            decoder.configure(inFmt, null, null, 0)
            encoder.configure(encFmt, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
            decoder.start(); encoder.start()
            muxer = MediaMuxer(out.absolutePath, muxFmt)
            var muxTrack = -1

            val info = MediaCodec.BufferInfo()
            var inputDone = false; var decodeDone = false; var encodeDone = false
            // PCM waiting for an encoder input buffer.
            var pending: ByteBuffer? = null
            var pcmBytesFed = 0L
            val bytesPerFrame = 2 * channels
            var eosQueued = false
            var idle = 0

            while (!encodeDone) {
                var progressed = false
                // 1. compressed source -> decoder
                if (!inputDone) {
                    val i = decoder.dequeueInputBuffer(TIMEOUT_US)
                    if (i >= 0) {
                        val buf = decoder.getInputBuffer(i)!!
                        val n = ex.readSampleData(buf, 0)
                        if (n < 0) {
                            decoder.queueInputBuffer(i, 0, 0, 0, MediaCodec.BUFFER_FLAG_END_OF_STREAM); inputDone = true
                        } else {
                            decoder.queueInputBuffer(i, 0, n, ex.sampleTime, 0); ex.advance()
                        }
                        progressed = true
                    }
                }
                // 2. decoder PCM -> pending
                if (!decodeDone && pending == null) {
                    val o = decoder.dequeueOutputBuffer(info, TIMEOUT_US)
                    if (o >= 0) {
                        val buf = decoder.getOutputBuffer(o)!!
                        if (info.size > 0) {
                            val copy = ByteBuffer.allocate(info.size).order(ByteOrder.nativeOrder())
                            buf.position(info.offset); buf.limit(info.offset + info.size)
                            copy.put(buf); copy.flip(); pending = copy
                        }
                        if (info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0) decodeDone = true
                        decoder.releaseOutputBuffer(o, false)
                        progressed = true
                    } else if (o == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                        val f = decoder.outputFormat
                        val ch = f.getInteger(MediaFormat.KEY_CHANNEL_COUNT)
                        val r = f.getInteger(MediaFormat.KEY_SAMPLE_RATE)
                        require(ch == channels && r == rate) { "decoder changed format to ${r}Hz/${ch}ch" }
                        if (f.containsKey(MediaFormat.KEY_PCM_ENCODING)) {
                            require(f.getInteger(MediaFormat.KEY_PCM_ENCODING) == android.media.AudioFormat.ENCODING_PCM_16BIT) {
                                "decoder output is not 16-bit PCM"
                            }
                        }
                        progressed = true
                    }
                }
                // 3. pending PCM -> encoder (split to fit its buffers)
                if (pending != null || (decodeDone && !eosQueued)) {
                    val i = encoder.dequeueInputBuffer(TIMEOUT_US)
                    if (i >= 0) {
                        val buf = encoder.getInputBuffer(i)!!
                        buf.clear()
                        val pts = pcmBytesFed / bytesPerFrame * 1_000_000L / rate
                        val p = pending
                        if (p != null) {
                            val n = minOf(p.remaining(), buf.remaining()) / bytesPerFrame * bytesPerFrame
                            val slice = p.duplicate(); slice.limit(slice.position() + n)
                            buf.put(slice); p.position(p.position() + n)
                            encoder.queueInputBuffer(i, 0, n, pts, 0)
                            pcmBytesFed += n
                            if (!p.hasRemaining()) pending = null
                        } else {
                            encoder.queueInputBuffer(i, 0, 0, pts, MediaCodec.BUFFER_FLAG_END_OF_STREAM); eosQueued = true
                        }
                        progressed = true
                    }
                }
                // 4. encoder -> muxer
                val o = encoder.dequeueOutputBuffer(info, TIMEOUT_US)
                if (o == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                    check(muxTrack < 0) { "encoder format changed twice" }
                    muxTrack = muxer.addTrack(encoder.outputFormat); muxer.start(); muxStarted = true
                    progressed = true
                } else if (o >= 0) {
                    val buf = encoder.getOutputBuffer(o)!!
                    val isConfig = info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG != 0
                    if (!isConfig && info.size > 0) {
                        check(muxStarted) { "encoder data before format" }
                        buf.position(info.offset); buf.limit(info.offset + info.size)
                        muxer.writeSampleData(muxTrack, buf, info)
                    }
                    if (info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0) encodeDone = true
                    encoder.releaseOutputBuffer(o, false)
                    progressed = true
                }
                idle = if (progressed) 0 else idle + 1
                check(idle < 3000) { "audio codec stalled" }   // ~30 s with no progress
            }
            check(muxStarted) { "encoder produced no format" }
            muxer.stop(); muxStarted = false     // a failing stop means an unfinished file: let it throw
        } finally {
            runCatching { decoder.stop() }; decoder.release()
            runCatching { encoder.stop() }; encoder.release()
            if (muxer != null) { if (muxStarted) runCatching { muxer.stop() }; runCatching { muxer.release() } }
        }
    }
}
