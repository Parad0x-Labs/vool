package systems.vool.capture

import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Finds what makes the screen freeze: every 100 ms it asks the screen thread for a sign of life, and when none
 * comes for 300 ms it writes where that thread is stuck (class and method names only, never data) to the log
 * (tag VoolCapture), and how long the freeze lasted once it ends.
 */
object MainWatch {
  @Volatile private var started = false

  fun start() {
    if (started) return
    started = true
    val main = Handler(Looper.getMainLooper())
    Thread({
      var stuckSince = 0L
      var reported = false
      while (true) {
        val alive = AtomicBoolean(false)
        val asked = SystemClock.uptimeMillis()
        main.post { alive.set(true) }
        SystemClock.sleep(100)
        while (!alive.get()) {
          val waited = SystemClock.uptimeMillis() - asked
          if (waited >= 300 && !reported) {
            reported = true
            stuckSince = asked
            val frames = Looper.getMainLooper().thread.stackTrace.take(14)
              .joinToString(" < ") { "${it.className.substringAfterLast('.')}.${it.methodName}:${it.lineNumber}" }
            Log.w("VoolCapture", "freeze: screen thread stuck ${waited} ms at $frames")
          }
          SystemClock.sleep(50)
        }
        if (reported) {
          Log.w("VoolCapture", "freeze: over after ${SystemClock.uptimeMillis() - stuckSince} ms")
          reported = false
        }
      }
    }, "vool-main-watch").apply { isDaemon = true; priority = Thread.MIN_PRIORITY }.start()
  }
}
