package systems.vool.capture

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.wifi.WifiManager
import android.os.Build
import android.os.IBinder
import android.os.PowerManager

/**
 * Keeps VOOL alive while it sends private captures to the computer, so a long video keeps going when
 * the phone locks or VOOL leaves the screen. Android shows a quiet notice while it runs. It holds no
 * data of its own: the sending itself stays in the app, and every byte it moves is already sealed.
 * Stops when the app says the queue is done, and on its own after an hour at most.
 */
class CaptureSendService : Service() {
  companion object {
    private const val CHANNEL = "vool_capture_send"
    private const val NOTICE = 4711
    private const val MAX_MS = 60 * 60_000L
    private const val TEXT = "text"

    /** Starts (or updates) the notice. Android allows starting it only while VOOL is on screen. */
    fun start(context: Context, text: String): Boolean = try {
      val intent = Intent(context, CaptureSendService::class.java).putExtra(TEXT, text)
      if (Build.VERSION.SDK_INT >= 26) context.startForegroundService(intent) else context.startService(intent)
      true
    } catch (_: Exception) {
      // started from the background (not allowed on newer Android): sending simply waits for the app
      false
    }

    fun stop(context: Context) {
      context.stopService(Intent(context, CaptureSendService::class.java))
    }
  }

  private var wake: PowerManager.WakeLock? = null
  private var wifi: WifiManager.WifiLock? = null

  override fun onBind(intent: Intent?): IBinder? = null

  override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
    val text = intent?.getStringExtra(TEXT) ?: "Sending to your computer"
    val manager = getSystemService(NotificationManager::class.java)
    if (Build.VERSION.SDK_INT >= 26 && manager.getNotificationChannel(CHANNEL) == null) {
      manager.createNotificationChannel(NotificationChannel(CHANNEL, "VOOL", NotificationManager.IMPORTANCE_LOW).apply {
        setShowBadge(false)
      })
    }
    val open = packageManager.getLaunchIntentForPackage(packageName)?.let {
      PendingIntent.getActivity(this, 0, it, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
    }
    val builder = if (Build.VERSION.SDK_INT >= 26) Notification.Builder(this, CHANNEL) else @Suppress("DEPRECATION") Notification.Builder(this)
    val notice = builder
      .setSmallIcon(R.drawable.ic_vool_capture_tile)
      .setContentTitle("VOOL")
      .setContentText(text)
      .setOngoing(true)
      .setVisibility(Notification.VISIBILITY_PUBLIC)
      .apply { if (open != null) setContentIntent(open) }
      .build()
    try {
      if (Build.VERSION.SDK_INT >= 29) startForeground(NOTICE, notice, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
      else startForeground(NOTICE, notice)
    } catch (_: Exception) {
      stopSelf()
      return START_NOT_STICKY
    }
    if (wake == null) {
      wake = (getSystemService(Context.POWER_SERVICE) as PowerManager)
        .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "vool:capture-send").apply { setReferenceCounted(false); acquire(MAX_MS) }
    }
    if (wifi == null) {
      @Suppress("DEPRECATION")
      wifi = (applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager)
        .createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "vool:capture-send").apply { setReferenceCounted(false); acquire() }
    }
    return START_NOT_STICKY
  }

  override fun onTimeout(startId: Int) {
    stopSelf()
  }

  override fun onTimeout(startId: Int, fgsType: Int) {
    stopSelf()
  }

  override fun onDestroy() {
    wake?.let { if (it.isHeld) it.release() }
    wifi?.let { if (it.isHeld) it.release() }
    wake = null
    wifi = null
    super.onDestroy()
  }
}
