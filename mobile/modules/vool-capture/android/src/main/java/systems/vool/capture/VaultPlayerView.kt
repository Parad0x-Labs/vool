package systems.vool.capture

import android.content.Context
import android.net.Uri
import android.view.ViewGroup
import androidx.annotation.OptIn
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.BaseDataSource
import androidx.media3.datasource.DataSource
import androidx.media3.datasource.DataSpec
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.ProgressiveMediaSource
import androidx.media3.ui.PlayerView
import expo.modules.kotlin.AppContext
import expo.modules.kotlin.viewevent.EventDispatcher
import expo.modules.kotlin.views.ExpoView

/** Reads a Vault stream ("vault://<id>") for the player: no network, no cache, nothing plain on disk. */
@OptIn(UnstableApi::class)
class VaultDataSource : BaseDataSource(false) {
  private var stream: VaultStreams.Stream? = null
  private var uri: Uri? = null
  private var position = 0L
  private var left = 0L
  private var opened = false

  override fun open(dataSpec: DataSpec): Long {
    uri = dataSpec.uri
    val found = VaultStreams.get(dataSpec.uri.host ?: "") ?: throw java.io.IOException("stream closed")
    stream = found
    transferInitializing(dataSpec)
    position = dataSpec.position
    left = if (dataSpec.length != C.LENGTH_UNSET.toLong()) dataSpec.length else found.size - position
    opened = true
    transferStarted(dataSpec)
    return left
  }

  override fun read(buffer: ByteArray, offset: Int, length: Int): Int {
    if (length == 0) return 0
    if (left <= 0) return C.RESULT_END_OF_INPUT
    val count = VaultStreams.read(stream ?: return C.RESULT_END_OF_INPUT, position, buffer, offset, minOf(length.toLong(), left).toInt())
    if (count < 0) return C.RESULT_END_OF_INPUT
    position += count
    left -= count
    bytesTransferred(count)
    return count
  }

  override fun getUri(): Uri? = uri

  override fun close() {
    if (opened) { opened = false; transferEnded() }
    stream = null
  }

  class Factory : DataSource.Factory {
    override fun createDataSource(): DataSource = VaultDataSource()
  }
}

/** Plays a video or a recording from a Vault stream, with the system player's own controls. */
@OptIn(UnstableApi::class)
class VaultPlayerView(context: Context, appContext: AppContext) : ExpoView(context, appContext) {
  override val shouldUseAndroidLayout = true
  private val onPlayerError by EventDispatcher()
  private val onPlayerReady by EventDispatcher()
  private val playerView = PlayerView(context).also {
    it.setShowNextButton(false); it.setShowPreviousButton(false)
    addView(it, ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
  }
  private var player: ExoPlayer? = null
  private var streamId = ""

  fun setStream(id: String) {
    if (id == streamId) return
    release()
    streamId = id
    if (id.isEmpty()) return
    val next = ExoPlayer.Builder(context)
      .setMediaSourceFactory(ProgressiveMediaSource.Factory(VaultDataSource.Factory()))
      .build()
    next.addListener(object : Player.Listener {
      override fun onPlayerError(error: PlaybackException) { onPlayerError(mapOf("message" to (error.errorCodeName ?: "error"))) }
      override fun onPlaybackStateChanged(state: Int) { if (state == Player.STATE_READY) onPlayerReady(mapOf("ready" to true)) }
    })
    next.setMediaItem(MediaItem.fromUri("vault://$id"))
    next.prepare()
    next.playWhenReady = true
    playerView.player = next
    player = next
  }

  fun release() {
    playerView.player = null
    player?.release()
    player = null
  }

  override fun onDetachedFromWindow() {
    super.onDetachedFromWindow()
    release()
    streamId = ""
  }
}
