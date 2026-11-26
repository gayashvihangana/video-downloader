import AVFoundation
import Flutter
import JavaScriptCore
import UIKit
import WebKit

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
    // JavaScript for YouTube's puzzles, and the YouTube login's cookies
    if let registrar = engineBridge.pluginRegistry.registrar(forPlugin: "NativePlugin") {
      NativePlugin.register(with: registrar)
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
          output: URL(fileURLWithPath: output),
          duration: Double(args["duration"] ?? "") ?? 0)
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

  /// [duration]: the real length in seconds as the site says (0 = unknown).
  static func merge(video: URL, audio: URL, output: URL, duration: Double = 0) async throws {
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

    // The result is as long as the shortest of: the picture, the sound and
    // the real length. A file's own length can be wrong (longer than what's
    // in it), which left a black, silent tail at the end.
    var length = CMTimeMinimum(videoRange.duration, audioRange.duration)
    if duration > 0 {
      // a little slack: the streams can run a few frames past the rounded length
      length = CMTimeMinimum(length, CMTime(seconds: duration + 1, preferredTimescale: 600))
    }

    let compositionVideo = composition.addMutableTrack(
      withMediaType: .video, preferredTrackID: kCMPersistentTrackID_Invalid)
    try compositionVideo?.insertTimeRange(
      CMTimeRange(start: videoRange.start, duration: length), of: videoTrack, at: .zero)
    // Keep portrait videos (Shorts, TikTok) the right way up
    compositionVideo?.preferredTransform = try await videoTrack.load(.preferredTransform)

    let compositionAudio = composition.addMutableTrack(
      withMediaType: .audio, preferredTrackID: kCMPersistentTrackID_Invalid)
    try compositionAudio?.insertTimeRange(
      CMTimeRange(start: audioRange.start, duration: length), of: audioTrack, at: .zero)

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

/// "video_downloader/native":
/// - evalJs {code}: runs JavaScript on the phone's own engine (JavaScriptCore)
///   and returns the last value as text (services/js_engine.dart)
/// - youtubeCookies: the sign-in page's YouTube / Google cookies, including
///   the HttpOnly ones only the app can read (services/account.dart)
class NativePlugin: NSObject, FlutterPlugin {
  static func register(with registrar: FlutterPluginRegistrar) {
    let channel = FlutterMethodChannel(
      name: "video_downloader/native", binaryMessenger: registrar.messenger())
    registrar.addMethodCallDelegate(NativePlugin(), channel: channel)
  }

  private let jsQueue = DispatchQueue(label: "video_downloader.js", qos: .userInitiated)

  func handle(_ call: FlutterMethodCall, result: @escaping FlutterResult) {
    switch call.method {
    case "evalJs":
      guard let code = (call.arguments as? [String: Any])?["code"] as? String else {
        result(FlutterError(code: "bad_args", message: "No code", details: nil))
        return
      }
      jsQueue.async {
        // A fresh engine each time: nothing is kept between puzzles
        guard let context = JSContext() else {
          DispatchQueue.main.async {
            result(FlutterError(code: "js_failed", message: "No JavaScript engine", details: nil))
          }
          return
        }
        var failure: String?
        context.exceptionHandler = { _, exception in
          if failure == nil { failure = exception?.toString() }
        }
        let value = context.evaluateScript(code)
        let output = value?.isString == true ? value?.toString() : nil
        DispatchQueue.main.async {
          if let output = output {
            result(output)
          } else {
            result(FlutterError(
              code: "js_failed", message: failure ?? "The script gave no output", details: nil))
          }
        }
      }

    case "youtubeCookies":
      WKWebsiteDataStore.default().httpCookieStore.getAllCookies { cookies in
        let wanted = cookies.filter {
          $0.domain.hasSuffix("youtube.com") || $0.domain.hasSuffix("google.com")
        }
        result(wanted.map { cookie in
          [
            "name": cookie.name,
            "value": cookie.value,
            "domain": cookie.domain,
            "path": cookie.path,
            "secure": cookie.isSecure,
            "expires": cookie.expiresDate.map { Int($0.timeIntervalSince1970) } ?? 0,
          ] as [String: Any]
        })
      }

    default:
      result(FlutterMethodNotImplemented)
    }
  }
}
