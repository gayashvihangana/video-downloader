import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';

import 'package:background_downloader/background_downloader.dart';
import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../models/download_item.dart';
import 'media.dart';
import 'python_api.dart';

/// All downloads: current and recent, kept between launches.
///
/// For each link: the Python side (yt-dlp) says which files to fetch; the
/// phone's background downloader fetches them (and keeps going in the
/// background); then they're joined and saved to the gallery.
class Downloads extends ChangeNotifier {
  Downloads(this._prefs);

  static const _key = 'downloads';
  static const maxAtOnce = 3; // downloads running at once (as on desktop)
  static const historyLimit = 100;

  final SharedPreferences _prefs;
  final List<DownloadItem> items = []; // newest first
  final Map<String, Set<String>> _finishedParts = {}; // item id -> task ids
  StreamSubscription<TaskUpdate>? _updates;

  bool wifiOnly = false;

  List<DownloadItem> get current =>
      items.where((item) => item.state.active).toList();
  List<DownloadItem> get recent =>
      items.where((item) => !item.state.active).toList();

  /// Load the list and pick up downloads that ran while the app was closed.
  Future<void> start() async {
    for (final json in jsonDecode(_prefs.getString(_key) ?? '[]') as List) {
      try {
        items.add(DownloadItem.fromJson(Map<String, dynamic>.from(json as Map)));
      } catch (_) {
        // an unreadable old entry isn't worth failing over
      }
    }

    final downloader = FileDownloader();
    await downloader.start();
    // Each download has up to 2 parts (video + sound)
    await downloader.configure(
      globalConfig: [
        (Config.holdingQueue, (maxAtOnce * 2, null, null)),
      ],
    );
    downloader.configureNotification(
      running: const TaskNotification('Downloading', '{displayName}'),
      paused: const TaskNotification('Paused', '{displayName}'),
      error: const TaskNotification('Download failed', '{displayName}'),
      progressBar: true,
    );
    _updates = downloader.updates.listen(_onUpdate);

    for (final item in current) {
      unawaited(_reconnect(item));
    }
    notifyListeners();
  }

  @override
  void dispose() {
    _updates?.cancel();
    super.dispose();
  }

  // ---------- starting ----------

  /// Queue a link. [details] is what the preview already knows (title...).
  DownloadItem add({
    required String url,
    required String mode,
    int? height,
    String quality = '',
    String title = '',
    String site = '',
    String? thumbnailUrl,
  }) {
    final item = DownloadItem(
      id: _newId(),
      url: url,
      mode: mode,
      height: height,
      quality: quality,
      title: title,
      site: site,
      thumbnailUrl: thumbnailUrl,
    );
    items.insert(0, item);
    _changed();
    unawaited(_prepare(item));
    return item;
  }

  static String _newId() {
    final random = Random().nextInt(1 << 32).toRadixString(16);
    return '${DateTime.now().millisecondsSinceEpoch}-$random';
  }

  /// Ask yt-dlp for the files, then hand them to the background downloader.
  Future<void> _prepare(DownloadItem item) async {
    item
      ..state = DownloadState.preparing
      ..progress = 0
      ..partProgress.clear();
    _finishedParts.remove(item.id);
    _changed();

    try {
      final api = await PythonApi.instance;
      final result = await api.call(
        '/resolve',
        body: {'url': item.url, 'mode': item.mode, 'height': item.height},
      );
      if (item.state != DownloadState.preparing) return; // cancelled meanwhile

      if (result['error'] != null) {
        _fail(item, result['error'] as String);
        return;
      }

      item
        ..title = item.title.isNotEmpty ? item.title : result['title'] as String
        ..fileName = result['file_name'] as String
        ..thumbnailUrl ??= result['thumbnail_url'] as String?
        ..parts = [
          for (final part in result['media'] as List)
            MediaPart.fromJson(Map<String, dynamic>.from(part as Map)),
        ];
      item.size = item.parts.fold(0, (sum, part) => sum + part.size);
      item.state = DownloadState.waiting;
      _changed();

      for (final task in _tasks(item)) {
        if (!await FileDownloader().enqueue(task)) {
          _fail(item, "Couldn't start the download. Try again.");
          return;
        }
      }
    } catch (error) {
      _fail(item, error is PythonApiException ? error.message : '$error');
    }
  }

  List<DownloadTask> _tasks(DownloadItem item) => [
    for (var i = 0; i < item.parts.length; i++)
      partTask(
        item.id,
        i,
        item.parts[i],
        wifiOnly: wifiOnly,
        displayName: item.name,
      ),
  ];

  // ---------- progress from the background downloader ----------

  DownloadItem? _item(String id) {
    for (final item in items) {
      if (item.id == id) return item;
    }
    return null;
  }

  void _onUpdate(TaskUpdate update) {
    final item = _item(update.task.group);
    if (item == null || !item.state.active) return;

    switch (update) {
      case TaskProgressUpdate():
        if (update.progress < 0) return; // a status-like signal, not progress
        item.partProgress[update.task.taskId] = update.progress;
        _recomputeProgress(item);
        if (update.hasNetworkSpeed) item.speed = update.networkSpeed;
        if (update.hasTimeRemaining) item.timeLeft = update.timeRemaining;
        if (item.state == DownloadState.waiting) {
          item.state = DownloadState.downloading;
        }
        notifyListeners();

      case TaskStatusUpdate():
        _onStatus(item, update);
    }
  }

  void _recomputeProgress(DownloadItem item) {
    final total = item.size;
    var done = 0.0;
    for (var i = 0; i < item.parts.length; i++) {
      final progress = item.partProgress['${item.id}-$i'] ?? 0;
      done += total > 0
          ? progress * item.parts[i].size / total
          : progress / item.parts.length;
    }
    item.progress = done.clamp(0, 1);
  }

  void _onStatus(DownloadItem item, TaskStatusUpdate update) {
    switch (update.status) {
      case TaskStatus.running:
        if (item.state != DownloadState.paused) {
          item.state = DownloadState.downloading;
        }
      case TaskStatus.paused:
        item.state = DownloadState.paused;
      case TaskStatus.complete:
        item.partProgress[update.task.taskId] = 1;
        final finished = _finishedParts.putIfAbsent(item.id, () => {})
          ..add(update.task.taskId);
        if (finished.length == item.parts.length) {
          unawaited(_finish(item));
        }
      case TaskStatus.failed || TaskStatus.notFound:
        // Links from YouTube & co. expire after a few hours (a long pause):
        // ask yt-dlp for fresh ones once, and start again
        final code = update.responseStatusCode;
        final expired = update.status == TaskStatus.notFound ||
            code == 403 ||
            code == 410;
        if (expired && !item.retriedExpiredLinks) {
          item.retriedExpiredLinks = true;
          unawaited(_restart(item));
        } else {
          _fail(item, _friendly(update));
        }
      case TaskStatus.canceled:
        // Not the user's Cancel (that marks the item first): the system
        // stopped it. Try once more with fresh links, then give up.
        if (!item.retriedExpiredLinks) {
          item.retriedExpiredLinks = true;
          unawaited(_restart(item));
        } else {
          _fail(item, 'The download was stopped. Try again.');
        }
      case TaskStatus.enqueued || TaskStatus.waitingToRetry:
        break;
    }
    _changed();
  }

  String _friendly(TaskStatusUpdate update) {
    final code = update.responseStatusCode;
    if (code == 403 || code == 410) {
      return 'The site stopped the download. Try again.';
    }
    final description = update.exception?.description.toLowerCase() ?? '';
    if (description.contains('connect') ||
        description.contains('host') ||
        description.contains('network') ||
        description.contains('timeout')) {
      return "Can't reach the site. Check your internet connection and try "
          'again.';
    }
    if (description.contains('space')) {
      return 'Your phone is out of storage space.';
    }
    return 'The download stopped. Try again.';
  }

  Future<void> _restart(DownloadItem item) async {
    await FileDownloader().cancelTasksWithIds(_taskIds(item));
    await _deleteWork(item);
    await _prepare(item);
  }

  /// All parts are here: join them and save to the gallery.
  Future<void> _finish(DownloadItem item) async {
    item.state = DownloadState.finishing;
    _changed();

    try {
      item.savedTo = await finishDownload(
        id: item.id,
        tasks: _tasks(item),
        fileName: item.fileName,
        audioOnly: item.audioOnly,
      );
      item
        ..state = DownloadState.done
        ..progress = 1
        ..finishedAt = DateTime.now();
      await FileDownloader().database.deleteRecordsWithIds(_taskIds(item));
    } catch (error) {
      _fail(item, error is MediaException ? error.message : '$error');
    }
    _changed();
  }

  /// After a restart of the app: find out what happened meanwhile.
  Future<void> _reconnect(DownloadItem item) async {
    switch (item.state) {
      case DownloadState.preparing:
        return _prepare(item);
      case DownloadState.finishing:
        return _finish(item);
      default:
        break;
    }

    final records = await FileDownloader().database.recordsForIds(
      _taskIds(item),
    );
    if (records.length < item.parts.length) {
      // Never got going (or the phone cleared it): fetch fresh links
      return _prepare(item);
    }

    for (final record in records) {
      item.partProgress[record.taskId] = record.progress.clamp(0, 1);
      switch (record.status) {
        case TaskStatus.complete:
          _finishedParts.putIfAbsent(item.id, () => {}).add(record.taskId);
        // Stopped while the app was closed: Android ends the app's background
        // work when it's short of memory, which shows up as "canceled". The
        // user's own Cancel never gets here (the item is already cancelled),
        // so start it again with fresh links.
        case TaskStatus.failed || TaskStatus.notFound || TaskStatus.canceled:
          return _restart(item);
        case TaskStatus.paused:
          item.state = DownloadState.paused;
        default:
          break;
      }
    }
    _recomputeProgress(item);

    if ((_finishedParts[item.id]?.length ?? 0) == item.parts.length) {
      return _finish(item);
    }
    _changed();
  }

  // ---------- buttons ----------

  Future<void> togglePause(DownloadItem item) async {
    if (item.state == DownloadState.paused) {
      item.state = DownloadState.downloading;
      _changed();
      for (final task in _unfinished(item)) {
        if (!await FileDownloader().resume(task)) {
          // The site doesn't allow resuming: start over with fresh links
          return _restart(item);
        }
      }
    } else if (item.state == DownloadState.downloading ||
        item.state == DownloadState.waiting) {
      item.state = DownloadState.paused;
      item.speed = 0;
      _changed();
      for (final task in _unfinished(item)) {
        await FileDownloader().pause(task);
      }
    }
  }

  Future<void> cancel(DownloadItem item) async {
    if (!item.state.active) return;
    _markCancelled(item); // first, so the "canceled" updates are ignored
    await FileDownloader().cancelTasksWithIds(_taskIds(item));
    await _deleteWork(item);
  }

  void _markCancelled(DownloadItem item) {
    item
      ..state = DownloadState.cancelled
      ..speed = 0
      ..finishedAt = DateTime.now();
    _changed();
  }

  void _fail(DownloadItem item, String error) {
    item
      ..state = DownloadState.failed
      ..error = error
      ..speed = 0
      ..finishedAt = DateTime.now();
    unawaited(_deleteWork(item));
    _changed();
  }

  /// Download again with the same choices; it moves back to Current.
  void retry(DownloadItem item) {
    items.remove(item);
    add(
      url: item.url,
      mode: item.mode,
      height: item.height,
      quality: item.quality,
      title: item.title,
      site: item.site,
      thumbnailUrl: item.thumbnailUrl,
    );
  }

  void retryAllFailed() {
    for (final item in recent.where((i) => i.state == DownloadState.failed)) {
      retry(item);
    }
  }

  void remove(DownloadItem item) {
    items.remove(item);
    _changed();
  }

  void clearRecent() {
    items.removeWhere((item) => !item.state.active);
    _changed();
  }

  /// Open a finished download in the phone's player.
  Future<bool> open(DownloadItem item) async {
    var path = item.savedTo;
    if (path == null) return false;
    if (Platform.isIOS && !path.startsWith('/')) {
      // On iPhone the gallery gives an identifier, not a path
      path = await FileDownloader().pathInSharedStorage(
        path,
        item.audioOnly ? SharedStorage.audio : SharedStorage.video,
      );
      if (path == null) return false;
    }
    return FileDownloader().openFile(filePath: path);
  }

  // ---------- helpers ----------

  List<String> _taskIds(DownloadItem item) => [
    for (var i = 0; i < max(item.parts.length, 2); i++) '${item.id}-$i',
  ];

  List<DownloadTask> _unfinished(DownloadItem item) {
    final finished = _finishedParts[item.id] ?? const {};
    return _tasks(item).where((t) => !finished.contains(t.taskId)).toList();
  }

  Future<void> _deleteWork(DownloadItem item) async {
    try {
      final folder = await workFolder(item.id);
      if (await folder.exists()) await folder.delete(recursive: true);
    } catch (_) {
      // leftovers are cleaned up with the app's data; not worth an error
    }
  }

  /// Save and tell the screens.
  void _changed() {
    // Keep every current download and the newest finished ones, in order
    var finished = 0;
    final keep = [
      for (final item in items)
        if (item.state.active || finished++ < historyLimit) item,
    ];
    _prefs.setString(
      _key,
      jsonEncode([for (final item in keep) item.toJson()]),
    );
    notifyListeners();
  }
}
