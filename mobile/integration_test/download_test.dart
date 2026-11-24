// Runs on the Android emulator: the phone's own background downloader fetches
// a video stream and a sound stream, they're joined natively into one MP4,
// and the result is saved to the Gallery.
//
// Needs the test files served from the PC:
//   python -m http.server 8765 --directory <folder with video.mp4, audio.m4a>
//   python tool/build.py android --skip-package test integration_test/download_test.dart
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

    final progress = <double>[];
    for (final task in tasks) {
      final result = await FileDownloader().download(
        task,
        onProgress: progress.add,
      );
      expect(result.status, TaskStatus.complete, reason: '${result.exception}');
    }
    expect(progress, isNotEmpty);

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
