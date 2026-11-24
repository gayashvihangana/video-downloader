// Runs on the Android emulator: the phone's own background downloader fetches
// a video stream and a sound stream, they're joined natively into one MP4,
// and the result is saved to the Gallery.
//
// Needs the test files (integration_test/media: 4 s of H.264 video without
// sound, and 4 s of AAC sound) served from the computer, with byte ranges
// (the video is fetched in pieces, as YouTube files are):
//   python tool/serve_media.py 8765
//   python tool/build.py android --skip-package test integration_test/download_test.dart
// The Android emulator reaches the computer at 10.0.2.2; the iOS simulator
// at 127.0.0.1 (--dart-define=TEST_SERVER=http://127.0.0.1:8765).
import 'dart:io';

import 'package:background_downloader/background_downloader.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:video_downloader/services/media.dart';

const server = String.fromEnvironment(
  'TEST_SERVER',
  defaultValue: 'http://10.0.2.2:8765',
);

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  setUpAll(() async {
    await FileDownloader().start(autoCleanDatabase: true);
  });

  test('video + sound are downloaded, joined and saved', () async {
    final id = 'test-${DateTime.now().millisecondsSinceEpoch}';
    final parts = [
      const MediaPart(
        url: '$server/video.mp4',
        headers: {'User-Agent': 'test'},
        ext: 'mp4',
        size: 28436,
        kind: 'video',
        chunkSize: 10000, // 3 pieces
      ),
      const MediaPart(
        url: '$server/audio.m4a',
        headers: {},
        ext: 'm4a',
        size: 65092,
        kind: 'audio',
      ),
    ];

    final tasks = [
      for (var i = 0; i < parts.length; i++) partTask(id, i, parts[i]),
    ];

    expect(tasks[0], isA<ParallelDownloadTask>());
    expect((tasks[0] as ParallelDownloadTask).chunks, 3);
    expect(tasks[1], isNot(isA<ParallelDownloadTask>()));

    final progress = <double>[];
    for (final task in tasks) {
      final result = await FileDownloader().download(
        task,
        onProgress: progress.add,
      );
      expect(result.status, TaskStatus.complete, reason: '${result.exception}');
    }
    expect(progress, isNotEmpty);

    // The pieces were put back together exactly
    final video = File(await tasks[0].filePath());
    expect(await video.length(), 28436);
    final original = await HttpClient()
        .getUrl(Uri.parse('$server/video.mp4'))
        .then((request) => request.close())
        .then(
          (response) => response.fold<List<int>>([], (a, b) => a..addAll(b)),
        );
    expect(await video.readAsBytes(), original);

    // Merge on its own first, to check the joined file
    final folder = await workFolder(id);
    final joined = '${folder.path}/joined.mp4';
    await Merger.merge(
      video: await tasks[0].filePath(),
      audio: await tasks[1].filePath(),
      output: joined,
    );
    final size = await File(joined).length();
    // ignore: avoid_print
    print('joined MP4: $size bytes');
    expect(size, greaterThan(28436 + 50000)); // has both picture and sound
    await File(joined).delete();

    // The whole finish: join + save to the Gallery
    final saved = await finishDownload(
      id: id,
      tasks: tasks,
      fileName: 'Test video [test].mp4',
      audioOnly: false,
    );
    // ignore: avoid_print
    print('saved to: $saved');
    expect(saved, isNotNull);
    expect(await folder.exists(), isFalse); // parts cleaned up
  }, timeout: const Timeout(Duration(minutes: 3)));

  test('merging a file without a video track gives a clear error', () async {
    final id = 'bad-${DateTime.now().millisecondsSinceEpoch}';
    final task = partTask(
      id,
      0,
      const MediaPart(
        url: '$server/audio.m4a',
        headers: {},
        ext: 'm4a',
        size: 65092,
        kind: 'audio',
      ),
    );
    final result = await FileDownloader().download(task);
    expect(result.status, TaskStatus.complete);

    final path = await task.filePath();
    final folder = await workFolder(id);
    await expectLater(
      Merger.merge(video: path, audio: path, output: '${folder.path}/x.mp4'),
      throwsA(isA<MediaException>()),
    );
    await folder.delete(recursive: true);
  });
}
