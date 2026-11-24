package io.github.gayashvihangana.video_downloader

import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine

class MainActivity : FlutterActivity() {
    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        // The app's own native piece: video + audio -> one MP4
        flutterEngine.plugins.add(MergePlugin())
    }
}
