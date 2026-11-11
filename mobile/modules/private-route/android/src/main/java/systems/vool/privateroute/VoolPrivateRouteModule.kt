package systems.vool.privateroute

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.os.IBinder
import android.os.SystemClock
import expo.modules.kotlin.Promise
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition
import okhttp3.Dns
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.torproject.jni.TorService
import java.io.IOException
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.Proxy
import java.net.UnknownHostException
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * Away from home: reaches the paired computer's hidden address through Tor running inside this app.
 *
 * Tor starts only when the app asks for a hidden address (the owner switched "away from home" on
 * and the pairing code carried one), and stops when the app says it is back on the home network.
 * Only http://<56 chars>.onion is ever sent here, and the name is handed to Tor unresolved, so
 * the phone's network never sees a lookup for it.
 */
class VoolPrivateRouteModule : Module() {
  private val workers = Executors.newCachedThreadPool()
  private val lock = Any()
  private var connection: ServiceConnection? = null
  @Volatile private var service: TorService? = null
  private var client: OkHttpClient? = null
  private var clientPort = 0

  private val hidden = Regex("^http://[a-z2-7]{56}\\.onion(/[A-Za-z0-9/_.-]*)?$")

  /** A lookup on the phone's own network would give the address away: refuse every one. */
  private object NoLookups : Dns {
    override fun lookup(hostname: String): List<InetAddress> = throw UnknownHostException("no lookups on the private route")
  }

  private fun context(): Context = appContext.reactContext?.applicationContext ?: throw IOException("app is closing")

  private fun bind(deadline: Long): TorService {
    service?.let { return it }
    val ready = CountDownLatch(1)
    val made = object : ServiceConnection {
      override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
        service = (binder as? TorService.LocalBinder)?.service
        ready.countDown()
      }
      override fun onServiceDisconnected(name: ComponentName?) { service = null }
    }
    synchronized(lock) {
      if (connection == null) {
        val context = context()
        if (!context.bindService(Intent(context, TorService::class.java), made, Context.BIND_AUTO_CREATE)) {
          runCatching { context.unbindService(made) }
          throw IOException("private route could not start")
        }
        connection = made
      }
    }
    while (service == null) {
      if (SystemClock.elapsedRealtime() > deadline) {
        stopRoute() // never came up: start clean next time
        throw IOException("private route did not start in time")
      }
      ready.await(250, TimeUnit.MILLISECONDS)
    }
    return service ?: throw IOException("private route stopped")
  }

  /** Tor's local port, once Tor has a working path to the network. */
  private fun readyPort(deadline: Long): Int {
    val tor = bind(deadline)
    while (true) {
      val port = tor.socksPort
      if (port > 0 && tor.getInfo("status/circuit-established")?.trim() == "1") return port
      if (service == null) throw IOException("private route stopped")
      if (SystemClock.elapsedRealtime() > deadline) throw IOException("private route did not connect in time")
      Thread.sleep(400)
    }
  }

  private fun clientFor(port: Int): OkHttpClient = synchronized(lock) {
    client?.takeIf { clientPort == port } ?: OkHttpClient.Builder()
      .proxy(Proxy(Proxy.Type.SOCKS, InetSocketAddress.createUnresolved("127.0.0.1", port)))
      .dns(NoLookups)
      .followRedirects(false)
      .followSslRedirects(false)
      .retryOnConnectionFailure(false) // a request is never sent twice by this layer
      .build().also { client = it; clientPort = port }
  }

  private fun stopRoute() {
    val old = synchronized(lock) {
      val held = connection
      connection = null
      service = null
      client = null
      clientPort = 0
      held
    }
    if (old != null) runCatching { context().unbindService(old) }
  }

  override fun definition() = ModuleDefinition {
    Name("VoolPrivateRoute")

    AsyncFunction("request") { url: String, method: String, headers: Map<String, String>, body: String?, timeoutMs: Double, promise: Promise ->
      workers.execute {
        try {
          require(hidden.matches(url)) { "not a hidden address" }
          require(method == "GET" || method == "POST") { "method" }
          val wait = timeoutMs.toLong().coerceIn(5_000L, 600_000L)
          val deadline = SystemClock.elapsedRealtime() + wait
          val port = readyPort(deadline)
          val left = (deadline - SystemClock.elapsedRealtime()).coerceAtLeast(1_000L)
          val request = Request.Builder().url(url).apply {
            headers.forEach { (name, value) -> header(name, value) }
            if (method == "POST") post((body ?: "").toRequestBody("application/json".toMediaType())) else get()
          }.build()
          val call = clientFor(port).newBuilder()
            .callTimeout(left, TimeUnit.MILLISECONDS)
            .connectTimeout(left, TimeUnit.MILLISECONDS)
            .readTimeout(left, TimeUnit.MILLISECONDS)
            .build().newCall(request)
          call.execute().use { response ->
            val text = response.body?.source()?.let { source ->
              // A reply larger than the app ever expects is refused, not buffered.
              if (!source.request(16L * 1024 * 1024 + 1)) source.buffer.readUtf8() else throw IOException("reply too large")
            } ?: ""
            promise.resolve(mapOf("status" to response.code, "body" to text))
          }
        } catch (error: Throwable) {
          promise.reject("private_route_failed", error.message ?: "private route failed", error)
        }
      }
    }

    /** Wake the route and wait until it can carry a request, so the request itself is sent fresh. */
    AsyncFunction("ready") { timeoutMs: Double, promise: Promise ->
      workers.execute {
        try {
          readyPort(SystemClock.elapsedRealtime() + timeoutMs.toLong().coerceIn(5_000L, 600_000L))
          promise.resolve(true)
        } catch (error: Throwable) {
          promise.reject("private_route_failed", error.message ?: "private route failed", error)
        }
      }
    }

    AsyncFunction("stop") { -> stopRoute() }

    AsyncFunction("running") { -> service != null }

    OnDestroy { stopRoute() }
  }
}
