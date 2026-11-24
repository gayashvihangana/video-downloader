import 'dart:io';

import 'package:background_downloader/background_downloader.dart';
import 'package:flutter/services.dart';
import 'package:path_provider/path_provider.dart';

/// One file to fetch, as the Python side resolved it (core.mobile_media).
class MediaPart {
  const MediaPart({
    required this.url,
    required this.headers,
    required this.ext,
    required this.size,
    required this.kind,
  });

  factory MediaPart.fromJson(Map<String, dynamic> json) => MediaPart(
    url: json['url'] as String,
    headers: Map<String, String>.from(json['headers'] as Map? ?? const {}),
    ext: json['ext'] as String? ?? 'mp4',
    size: (json['size'] as num?)?.toInt() ?? 0,
    kind: json['kind'] as String? ?? 'video',
  );

  final String url;
  final Map<String, String> headers;
  final String ext;
  final int size;
  final String kind; // "video" (may include sound) or "audio"

  Map<String, dynamic> toJson() => {
    'url': url,
    'headers': headers,
    'ext': ext,
    'size': size,
    'kind': kind,
  };
}

/// Video + audio -> one MP4, natively (MergePlugin.kt / AppDelegate.swift).
class Merger {
  static const _channel = MethodChannel('video_downloader/merge');

  static Future<void> merge({
    required String video,
    required String audio,
    required String output,
  }) async {
    try {
      await _channel.invokeMethod<String>('merge', {
        'video': video,
        'audio': audio,
        'output': output,
      });
    } on PlatformException catch (error) {
      throw MediaException(
        error.message ?? "Couldn't join the video and the sound.",
      );
    }
  }
}

class MediaException implements Exception {
  const MediaException(this.message);
  final String message;

  @override
  String toString() => message;
}

/// Where a download's parts are kept until they're joined and saved.
Future<Directory> workFolder(String id) async {
  final base = await getApplicationSupportDirectory();
  return Directory('${base.path}/work/$id');
}

/// The background download of one part (survives the app being in the
/// background; pause / resume continue where it stopped).
DownloadTask partTask(String id, int index, MediaPart part, {bool wifiOnly = false}) {
  return DownloadTask(
    taskId: '$id-$index',
    url: part.url,
    headers: part.headers,
    filename: '${part.kind}.${part.ext}',
    directory: 'work/$id',
    baseDirectory: BaseDirectory.applicationSupport,
    group: id,
    updates: Updates.statusAndProgress,
    requiresWiFi: wifiOnly,
    retries: 3,
    allowPause: true,
    metaData: part.kind,
  );
}

/// Join the downloaded parts (if there are two) and put the result in the
/// Gallery / Photos (video) or Music (audio). Returns what to open later:
/// a file path, or on iOS a Photos identifier.
Future<String?> finishDownload({
  required String id,
  required List<DownloadTask> tasks,
  required String fileName,
  required bool audioOnly,
}) async {
  final folder = await workFolder(id);
  final output = '${folder.path}/$fileName';

  final paths = [for (final task in tasks) await task.filePath()];

  if (paths.length == 2) {
    final video = tasks.indexWhere((t) => t.metaData == 'video');
    await Merger.merge(
      video: paths[video],
      audio: paths[1 - video],
      output: output,
    );
  } else {
    await File(paths.single).rename(output);
  }

  final saved = await FileDownloader().moveFileToSharedStorage(
    output,
    audioOnly ? SharedStorage.audio : SharedStorage.video,
    directory: 'Video Downloader',
  );

  if (saved == null) {
    throw const MediaException(
      "Couldn't save to your gallery. Allow the app to add photos and videos "
      'in Settings, then try again.',
    );
  }

  // The parts aren't needed any more
  await folder.delete(recursive: true).catchError((_) => folder);
  return saved;
}
