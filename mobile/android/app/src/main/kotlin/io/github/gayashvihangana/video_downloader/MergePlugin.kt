package io.github.gayashvihangana.video_downloader

import android.media.MediaCodec
import android.media.MediaExtractor
import android.media.MediaFormat
import android.media.MediaMuxer
import android.os.Handler
import android.os.Looper
import io.flutter.embedding.engine.plugins.FlutterPlugin
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel
import java.io.File
import java.nio.ByteBuffer
import java.util.concurrent.Executors

/**
 * Joins a downloaded H.264 video stream and an AAC audio stream into one MP4,
 * copying the data as-is (no re-encoding, so it's fast and lossless).
 *
 * Dart: MethodChannel("video_downloader/merge").invokeMethod("merge",
 *       {"video": path, "audio": path, "output": path})
 */
class MergePlugin : FlutterPlugin, MethodChannel.MethodCallHandler {
    private lateinit var channel: MethodChannel
    private val worker = Executors.newSingleThreadExecutor()
    private val main = Handler(Looper.getMainLooper())

    override fun onAttachedToEngine(binding: FlutterPlugin.FlutterPluginBinding) {
        channel = MethodChannel(binding.binaryMessenger, "video_downloader/merge")
        channel.setMethodCallHandler(this)
    }

    override fun onDetachedFromEngine(binding: FlutterPlugin.FlutterPluginBinding) {
        channel.setMethodCallHandler(null)
    }

    override fun onMethodCall(call: MethodCall, result: MethodChannel.Result) {
        if (call.method != "merge") {
            result.notImplemented()
            return
        }
        val video = call.argument<String>("video")!!
        val audio = call.argument<String>("audio")!!
        val output = call.argument<String>("output")!!

        // Off the UI thread: big files take a few seconds
        worker.execute {
            try {
                merge(video, audio, output)
                main.post { result.success(output) }
            } catch (error: Exception) {
                File(output).delete()
                main.post { result.error("merge_failed", error.message, null) }
            }
        }
    }

    private fun merge(videoPath: String, audioPath: String, outputPath: String) {
        val muxer = MediaMuxer(outputPath, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4)
        val sources = listOf(videoPath to "video/", audioPath to "audio/")
        val tracks = mutableListOf<Pair<MediaExtractor, Int>>()

        try {
            for ((path, kind) in sources) {
                val extractor = MediaExtractor()
                extractor.setDataSource(path)
                val index = (0 until extractor.trackCount).firstOrNull {
                    extractor.getTrackFormat(it).getString(MediaFormat.KEY_MIME)
                        ?.startsWith(kind) == true
                } ?: throw IllegalStateException("No $kind track in $path")

                extractor.selectTrack(index)
                val format = extractor.getTrackFormat(index)
                if (kind == "video/" && format.containsKey(MediaFormat.KEY_ROTATION)) {
                    muxer.setOrientationHint(format.getInteger(MediaFormat.KEY_ROTATION))
                }
                tracks += extractor to muxer.addTrack(format)
            }

            muxer.start()
            val buffer = ByteBuffer.allocate(4 * 1024 * 1024)
            val info = MediaCodec.BufferInfo()

            for ((extractor, track) in tracks) {
                while (true) {
                    val size = extractor.readSampleData(buffer, 0)
                    if (size < 0) break
                    info.set(0, size, extractor.sampleTime, toMuxerFlags(extractor.sampleFlags))
                    muxer.writeSampleData(track, buffer, info)
                    extractor.advance()
                }
            }
            muxer.stop()
        } finally {
            tracks.forEach { it.first.release() }
            muxer.release()
        }
    }

    private fun toMuxerFlags(sampleFlags: Int): Int {
        var flags = 0
        if (sampleFlags and MediaExtractor.SAMPLE_FLAG_SYNC != 0) {
            flags = flags or MediaCodec.BUFFER_FLAG_KEY_FRAME
        }
        return flags
    }
}
