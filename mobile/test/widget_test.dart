// Fast tests (no device): formatting, saved downloads, and the Downloads
// screen. Device tests (Python, downloading, merging) are in integration_test.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:video_downloader/models/download_item.dart';
import 'package:video_downloader/screens/downloads.dart';
import 'package:video_downloader/services/downloads.dart';
import 'package:video_downloader/services/media.dart';
import 'package:video_downloader/util/format.dart';

void main() {
  group('sizes and times, like the desktop app', () {
    test('sizes', () {
      expect(humanSize(0), '');
      expect(humanSize(999), '999 B');
      expect(humanSize(1000), '1 KB');
      expect(humanSize(9950000), '9.9 MB');
      expect(humanSize(85123000), '85 MB');
      expect(humanSize(1530000000), '1.5 GB');
    });

    test('times', () {
      expect(humanTime(0), '0:00');
      expect(humanTime(59), '0:59');
      expect(humanTime(3599), '59:59');
      expect(humanTime(3600), '1:00:00');
    });

    test('when', () {
      final now = DateTime(2026, 11, 24, 18);
      expect(when(DateTime(2026, 11, 24, 17, 30), now: now), '5:30 PM');
      expect(when(DateTime(2026, 11, 23, 9), now: now), 'Yesterday');
      expect(when(DateTime(2026, 11, 12), now: now), '12 Nov');
    });
  });

  group('a download', () {
    DownloadItem sample() => DownloadItem(
      id: '1',
      url: 'https://youtu.be/a',
      mode: 'Video',
      height: 1080,
      quality: '1080p',
      title: 'Sunset',
      site: 'YouTube',
      parts: const [
        MediaPart(
          url: 'https://cdn/v',
          headers: {'User-Agent': 'UA'},
          ext: 'mp4',
          size: 40000000,
          kind: 'video',
        ),
      ],
      size: 40000000,
    );

    test('is remembered between launches', () {
      final item = sample()
        ..state = DownloadState.done
        ..finishedAt = DateTime(2026, 11, 24, 17, 30);
      final again = DownloadItem.fromJson(item.toJson());

      expect(again.title, 'Sunset');
      expect(again.state, DownloadState.done);
      expect(again.parts.single.headers['User-Agent'], 'UA');
      expect(again.finishedAt, item.finishedAt);
    });

    test('says what it is doing', () {
      final item = sample()
        ..state = DownloadState.downloading
        ..progress = 0.5
        ..speed = 2.5
        ..timeLeft = const Duration(seconds: 65);

      expect(item.meta, 'YouTube  ·  Video  ·  1080p');
      expect(item.statusText, '50% · 20 MB of 40 MB · 2.5 MB/s · 1:05 left');

      item.state = DownloadState.paused;
      expect(item.statusText, 'Paused at 50%');

      item
        ..state = DownloadState.failed
        ..error = 'This video is private.';
      expect(item.statusText, '✕  This video is private.');
    });

    test('audio says M4A', () {
      final item = DownloadItem(
        id: '2',
        url: 'u',
        mode: 'Audio',
        site: 'TikTok',
      );
      expect(item.meta, 'TikTok  ·  Audio  ·  M4A');
    });
  });

  testWidgets('Downloads screen lists current and recent downloads', (
    tester,
  ) async {
    SharedPreferences.setMockInitialValues({});
    final downloads = Downloads(await SharedPreferences.getInstance());
    downloads.items.addAll([
      DownloadItem(
        id: 'a',
        url: 'https://a',
        mode: 'Video',
        quality: '720p',
        title: 'Running now',
        state: DownloadState.downloading,
        size: 100,
      )..progress = 0.25,
      DownloadItem(
        id: 'b',
        url: 'https://b',
        mode: 'Video',
        title: 'Broken one',
        state: DownloadState.failed,
        error: 'This video is private.',
      ),
    ]);

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: DownloadsScreen(downloads: downloads, onGoHome: () {}),
        ),
      ),
    );

    expect(find.text('Downloading'), findsOneWidget);
    expect(find.text('Running now'), findsOneWidget);
    expect(find.byTooltip('Pause'), findsOneWidget);
    expect(find.text('Broken one'), findsOneWidget);
    expect(find.text('This video is private.'), findsOneWidget);
    expect(find.text('Retry Failed'), findsOneWidget);
    expect(find.byTooltip('Try again'), findsOneWidget);
  });
}
