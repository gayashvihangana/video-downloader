package io.github.gayashvihangana.video_downloader

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.webkit.CookieManager
import androidx.javascriptengine.IsolateStartupParameters
import androidx.javascriptengine.JavaScriptSandbox
import io.flutter.embedding.engine.plugins.FlutterPlugin
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * "video_downloader/native":
 * - evalJs {code}: runs JavaScript on the phone's own engine (JavaScriptSandbox,
 *   the V8 engine of Android System WebView) and returns the last value as
 *   text (services/js_engine.dart)
 * - youtubeCookies: the sign-in page's YouTube cookies, including the HttpOnly
 *   ones only the app can read (services/account.dart)
 */
class NativePlugin : FlutterPlugin, MethodChannel.MethodCallHandler {
    private lateinit var channel: MethodChannel
    private lateinit var context: Context
    private val main = Handler(Looper.getMainLooper())
    private val worker = Executors.newSingleThreadExecutor()

    override fun onAttachedToEngine(binding: FlutterPlugin.FlutterPluginBinding) {
        context = binding.applicationContext
        channel = MethodChannel(binding.binaryMessenger, "video_downloader/native")
        channel.setMethodCallHandler(this)
    }

    override fun onDetachedFromEngine(binding: FlutterPlugin.FlutterPluginBinding) {
        channel.setMethodCallHandler(null)
        worker.shutdown()
    }

    override fun onMethodCall(call: MethodCall, result: MethodChannel.Result) {
        when (call.method) {
            "evalJs" -> {
                val code = call.argument<String>("code")
                if (code == null) {
                    result.error("bad_args", "No code", null)
                    return
                }
                worker.execute { evalJs(code, result) }
            }
            "youtubeCookies" -> result.success(youtubeCookies())
            else -> result.notImplemented()
        }
    }

    private fun evalJs(code: String, result: MethodChannel.Result) {
        try {
            check(JavaScriptSandbox.isSupported()) {
                "This phone's web engine is too old: update Android System WebView."
            }
            // A fresh engine each time: nothing is kept between puzzles
            JavaScriptSandbox.createConnectedInstanceAsync(context).get(30, TimeUnit.SECONDS).use { sandbox ->
                // YouTube's player script is ~3 MB: more than Android may pass in one go
                check(sandbox.isFeatureSupported(JavaScriptSandbox.JS_FEATURE_EVALUATE_WITHOUT_TRANSACTION_LIMIT)) {
                    "This phone's web engine is too old: update Android System WebView."
                }
                val settings = IsolateStartupParameters()
                if (sandbox.isFeatureSupported(JavaScriptSandbox.JS_FEATURE_ISOLATE_MAX_HEAP_SIZE)) {
                    settings.maxHeapSizeBytes = 256L * 1024 * 1024
                }
                sandbox.createIsolate(settings).use { isolate ->
                    val output = isolate.evaluateJavaScriptAsync(code).get(90, TimeUnit.SECONDS)
                    main.post { result.success(output) }
                }
            }
        } catch (error: Exception) {
            val message = (error.cause ?: error).message ?: "JavaScript failed"
            main.post { result.error("js_failed", message, null) }
        }
    }

    private fun youtubeCookies(): List<Map<String, Any>> {
        // Android gives "name=value; name=value" for a site (HttpOnly ones too)
        val raw = CookieManager.getInstance().getCookie("https://www.youtube.com") ?: return emptyList()
        return raw.split(";").mapNotNull { part ->
            val pair = part.trim()
            val equals = pair.indexOf('=')
            if (equals <= 0) return@mapNotNull null
            mapOf(
                "name" to pair.substring(0, equals),
                "value" to pair.substring(equals + 1),
                "domain" to ".youtube.com",
                "path" to "/",
                "secure" to true,
                "expires" to 0,
            )
        }
    }
}
