package io.github.gayashvihangana.video_downloader

import android.content.Intent
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    private var share: MethodChannel? = null

    companion object {
        // Kept for the whole app, not this screen: Android may re-create the
        // screen right after a share opens the app, and the link must survive
        private var pendingLink: String? = null
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        // The app's own native piece: video + audio -> one MP4
        flutterEngine.plugins.add(MergePlugin())
        // JavaScript for YouTube's puzzles, and the YouTube login's cookies
        flutterEngine.plugins.add(NativePlugin())

        // The app was opened by a share
        sharedLink(intent)?.let { pendingLink = it }

        // "Share -> Video Downloader" from YouTube, TikTok, Instagram...
        share = MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "video_downloader/share")
            .apply {
                setMethodCallHandler { call, result ->
                    if (call.method == "initialLink") {
                        result.success(pendingLink)
                        pendingLink = null
                    } else {
                        result.notImplemented()
                    }
                }
            }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        // The app was already open: hand the link over straight away
        sharedLink(intent)?.let { share?.invokeMethod("shared", it) }
    }

    private fun sharedLink(intent: Intent?): String? {
        if (intent?.action != Intent.ACTION_SEND || intent.type != "text/plain") return null
        // Apps share "Title https://..." or just the link: keep the link
        val text = intent.getStringExtra(Intent.EXTRA_TEXT) ?: return null
        return Regex("""https?://\S+""").find(text)?.value ?: text.trim()
    }
}
