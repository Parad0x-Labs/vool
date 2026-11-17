package systems.vool.capture

import android.app.PendingIntent
import android.os.Build
import android.service.quicksettings.Tile
import android.service.quicksettings.TileService

/** Quick settings tile: one tap opens VOOL's private camera. VOOL's own lock still asks first. */
class CaptureTileService : TileService() {
  override fun onStartListening() {
    qsTile?.let {
      it.state = Tile.STATE_INACTIVE
      it.updateTile()
    }
  }

  override fun onClick() {
    if (isLocked) unlockAndRun { open() } else open()
  }

  private fun open() {
    val intent = CameraLauncherActivity.captureIntent(this)
    if (Build.VERSION.SDK_INT >= 34) {
      startActivityAndCollapse(PendingIntent.getActivity(this, 0, intent, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT))
    } else {
      @Suppress("DEPRECATION")
      startActivityAndCollapse(intent)
    }
  }
}
