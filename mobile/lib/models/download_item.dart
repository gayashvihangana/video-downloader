import '../services/media.dart';
import '../util/format.dart';

/// What a download is doing.
enum DownloadState {
  preparing, // asking yt-dlp for the files
  waiting, // queued (up to 3 run at once)
  downloading,
  paused,
  finishing, // joining video + sound, saving to the gallery
  done,
  failed,
  cancelled;

  bool get active => index <= finishing.index;
}

/// One link being downloaded (or finished). Kept between app launches.
class DownloadItem {
  DownloadItem({
    required this.id,
    required this.url,
    required this.mode,
    this.height,
    this.quality = '',
    this.title = '',
    this.site = '',
    this.thumbnailUrl,
    this.state = DownloadState.preparing,
    this.fileName = '',
    this.parts = const [],
    this.savedTo,
    this.size = 0,
    this.error = '',
    this.finishedAt,
  });

  factory DownloadItem.fromJson(Map<String, dynamic> json) => DownloadItem(
    id: json['id'] as String,
    url: json['url'] as String,
    mode: json['mode'] as String? ?? 'Video',
    height: json['height'] as int?,
    quality: json['quality'] as String? ?? '',
    title: json['title'] as String? ?? '',
    site: json['site'] as String? ?? '',
    thumbnailUrl: json['thumbnailUrl'] as String?,
    state: DownloadState.values.asNameMap()[json['state']] ??
        DownloadState.failed,
    fileName: json['fileName'] as String? ?? '',
    parts: [
      for (final part in json['parts'] as List? ?? const [])
        MediaPart.fromJson(Map<String, dynamic>.from(part as Map)),
    ],
    savedTo: json['savedTo'] as String?,
    size: json['size'] as int? ?? 0,
    error: json['error'] as String? ?? '',
    finishedAt: json['finishedAt'] == null
        ? null
        : DateTime.fromMillisecondsSinceEpoch(json['finishedAt'] as int),
  );

  final String id;
  final String url;
  final String mode; // "Video" / "Audio" (as in core.py)
  final int? height; // exact pixel height to download (null = best)
  final String quality; // what the user picked, e.g. "1080p"

  String title;
  String site;
  String? thumbnailUrl;
  DownloadState state;
  String fileName;
  List<MediaPart> parts; // files to fetch (1, or video + sound)
  String? savedTo; // file path, or Photos identifier on iOS
  int size;
  String error;
  DateTime? finishedAt;

  // Live only (not saved)
  double progress = 0;
  double speed = 0; // MB/s
  Duration? timeLeft;
  final Map<String, double> partProgress = {};
  bool retriedExpiredLinks = false;

  bool get audioOnly => mode == 'Audio';

  String get name => title.isNotEmpty ? title : url;

  /// "YouTube · Video · 1080p"
  String get meta => [
    site,
    if (audioOnly) ...['Audio', 'M4A'] else ...['Video', quality],
  ].where((part) => part.isNotEmpty).join('  ·  ');

  /// The line under the progress bar / title.
  String get statusText {
    switch (state) {
      case DownloadState.preparing:
        return 'Getting ready…';
      case DownloadState.waiting:
        return 'Waiting to start…';
      case DownloadState.downloading:
        final details = <String>['${(progress * 100).floor()}%'];
        if (size > 0) {
          details.add('${humanSize(size * progress)} of ${humanSize(size)}');
        }
        if (speed > 0) details.add('${humanSize(speed * 1000000)}/s');
        if (timeLeft != null && timeLeft!.inSeconds > 0) {
          details.add('${humanTime(timeLeft!.inSeconds)} left');
        }
        return details.join('  ·  ');
      case DownloadState.paused:
        return 'Paused at ${(progress * 100).floor()}%';
      case DownloadState.finishing:
        return audioOnly ? 'Saving…' : 'Joining video and sound…';
      case DownloadState.done:
        return [
          '✓  Saved',
          if (size > 0) humanSize(size),
          when(finishedAt),
        ].join('  ·  ');
      case DownloadState.cancelled:
        return 'Cancelled  ·  ${when(finishedAt)}';
      case DownloadState.failed:
        return '✕  ${error.isEmpty ? 'Download failed.' : error}';
    }
  }

  Map<String, dynamic> toJson() => {
    'id': id,
    'url': url,
    'mode': mode,
    'height': height,
    'quality': quality,
    'title': title,
    'site': site,
    'thumbnailUrl': thumbnailUrl,
    'state': state.name,
    'fileName': fileName,
    'parts': [for (final part in parts) part.toJson()],
    'savedTo': savedTo,
    'size': size,
    'error': error,
    'finishedAt': finishedAt?.millisecondsSinceEpoch,
  };
}
