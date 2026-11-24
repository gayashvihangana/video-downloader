import AVFoundation
import Flutter
import UIKit

@main
@objc class AppDelegate: FlutterAppDelegate, FlutterImplicitEngineDelegate {
  override func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
  ) -> Bool {
    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }

  func didInitializeImplicitFlutterEngine(_ engineBridge: FlutterImplicitEngineBridge) {
    GeneratedPluginRegistrant.register(with: engineBridge.pluginRegistry)
    // The app's own native piece: video + audio -> one MP4
    if let registrar = engineBridge.pluginRegistry.registrar(forPlugin: "MergePlugin") {
      MergePlugin.register(with: registrar)
    }
  }
}

/// Joins a downloaded H.264 video stream and an AAC audio stream into one MP4,
/// copying the data as-is (passthrough: no re-encoding, fast and lossless).
///
/// Dart: MethodChannel("video_downloader/merge").invokeMethod("merge",
///       {"video": path, "audio": path, "output": path})
class MergePlugin: NSObject, FlutterPlugin {
  static func register(with registrar: FlutterPluginRegistrar) {
    let channel = FlutterMethodChannel(
      name: "video_downloader/merge", binaryMessenger: registrar.messenger())
    registrar.addMethodCallDelegate(MergePlugin(), channel: channel)
  }

  func handle(_ call: FlutterMethodCall, result: @escaping FlutterResult) {
    guard call.method == "merge",
      let args = call.arguments as? [String: String],
      let video = args["video"], let audio = args["audio"], let output = args["output"]
    else {
      result(FlutterMethodNotImplemented)
      return
    }

    Task {
      do {
        try await Self.merge(
          video: URL(fileURLWithPath: video),
          audio: URL(fileURLWithPath: audio),
          output: URL(fileURLWithPath: output))
        result(output)
      } catch {
        try? FileManager.default.removeItem(atPath: output)
        result(
          FlutterError(code: "merge_failed", message: error.localizedDescription, details: nil))
      }
    }
  }

  enum MergeError: LocalizedError {
    case missingTrack(String)
    case exportFailed(String)

    var errorDescription: String? {
      switch self {
      case .missingTrack(let kind): return "The downloaded \(kind) file has no \(kind) track."
      case .exportFailed(let reason): return "Couldn't join video and sound: \(reason)"
      }
    }
  }

  static func merge(video: URL, audio: URL, output: URL) async throws {
    let composition = AVMutableComposition()
    let videoAsset = AVURLAsset(url: video)
    let audioAsset = AVURLAsset(url: audio)

    guard let videoTrack = try await videoAsset.loadTracks(withMediaType: .video).first else {
      throw MergeError.missingTrack("video")
    }
    guard let audioTrack = try await audioAsset.loadTracks(withMediaType: .audio).first else {
      throw MergeError.missingTrack("audio")
    }

    let videoRange = try await videoTrack.load(.timeRange)
    let audioRange = try await audioTrack.load(.timeRange)

    let compositionVideo = composition.addMutableTrack(
      withMediaType: .video, preferredTrackID: kCMPersistentTrackID_Invalid)
    try compositionVideo?.insertTimeRange(videoRange, of: videoTrack, at: .zero)
    // Keep portrait videos (Shorts, TikTok) the right way up
    compositionVideo?.preferredTransform = try await videoTrack.load(.preferredTransform)

    let compositionAudio = composition.addMutableTrack(
      withMediaType: .audio, preferredTrackID: kCMPersistentTrackID_Invalid)
    // Sound can be a few milliseconds longer than the picture: trim it
    let audioDuration = CMTimeMinimum(audioRange.duration, videoRange.duration)
    try compositionAudio?.insertTimeRange(
      CMTimeRange(start: audioRange.start, duration: audioDuration), of: audioTrack, at: .zero)

    try? FileManager.default.removeItem(at: output)

    guard
      let export = AVAssetExportSession(
        asset: composition, presetName: AVAssetExportPresetPassthrough)
    else {
      throw MergeError.exportFailed("this video can't be copied as-is")
    }
    try await export.export(to: output, as: .mp4)
  }
}
