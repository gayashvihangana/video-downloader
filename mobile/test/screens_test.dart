// Every screen in its main states, at phone size, in light and dark: no
// overflow or layout errors. With SCREENSHOTS=1 it also saves PNGs with real
// fonts to build/screenshots (to look at the design without a phone):
//   SCREENSHOTS=1 flutter test test/screens_test.dart
import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:video_downloader/models/download_item.dart';
import 'package:video_downloader/screens/downloads.dart';
import 'package:video_downloader/screens/home.dart';
import 'package:video_downloader/screens/settings.dart';
import 'package:video_downloader/services/downloads.dart';
import 'package:video_downloader/services/settings.dart';
import 'package:video_downloader/ui/theme.dart';

final screenshots = Platform.environment['SCREENSHOTS'] == '1';
const phone = Size(393, 852); // a typical phone, in logical pixels

Future<void> loadFonts() async {
  final fonts =
      '${Platform.environment['FLUTTER_ROOT']}/bin/cache/artifacts/'
      'material_fonts';
  Future<ByteData> read(String name) async =>
      ByteData.sublistView(await File('$fonts/$name').readAsBytes());

  final roboto = FontLoader('Roboto');
  for (final weight in ['regular', 'medium', 'bold', 'black']) {
    roboto.addFont(read('roboto-$weight.ttf'));
  }
  await roboto.load();
  await (FontLoader(
    'MaterialIcons',
  )..addFont(read('materialicons-regular.otf'))).load();
}

const preview = {
  'url': 'https://www.youtube.com/watch?v=aqz-KE-bpKQ',
  'title': 'Big Buck Bunny — 60fps 4K short film',
  'site': 'YouTube',
  'uploader': 'Blender',
  'duration': 635,
  'thumbnail_url': null,
  'has_audio_only': true,
  'qualities': [
    {'label': '1080p', 'height': 1080, 'size': 182000000, 'approx': false},
    {'label': '720p', 'height': 720, 'size': 96000000, 'approx': false},
    {'label': '480p', 'height': 480, 'size': 51000000, 'approx': true},
    {'label': '360p', 'height': 360, 'size': 33000000, 'approx': false},
  ],
};

List<DownloadItem> sampleItems() => [
  DownloadItem(
      id: 'a',
      url: 'https://a',
      mode: 'Video',
      quality: '1080p',
      title: 'Big Buck Bunny — 60fps 4K short film',
      site: 'YouTube',
      state: DownloadState.downloading,
      size: 182000000,
    )
    ..progress = 0.62
    ..speed = 3.4
    ..timeLeft = const Duration(seconds: 21),
  DownloadItem(
    id: 'b',
    url: 'https://b',
    mode: 'Audio',
    title: 'Lo-fi beats to study to',
    site: 'TikTok',
    state: DownloadState.paused,
    size: 8000000,
  )..progress = 0.35,
  DownloadItem(
    id: 'c',
    url: 'https://c',
    mode: 'Video',
    quality: '720p',
    title: 'Sunset timelapse over the city',
    site: 'Instagram',
    state: DownloadState.done,
    size: 48200000,
    finishedAt: DateTime.now().subtract(const Duration(minutes: 20)),
  ),
  DownloadItem(
    id: 'd',
    url: 'https://d',
    mode: 'Video',
    quality: '480p',
    title: 'Private family video',
    site: 'Facebook',
    state: DownloadState.failed,
    error: 'This video is private.',
  ),
];

void main() {
  late Settings settings;
  late Downloads downloads;

  setUpAll(() async {
    if (screenshots) {
      await loadFonts();
    }
  });

  setUp(() async {
    SharedPreferences.setMockInitialValues({});
    PackageInfo.setMockInitialValues(
      appName: 'Video Downloader',
      packageName: 'x',
      version: '1.0.2',
      buildNumber: '3',
      buildSignature: '',
    );
    final prefs = await SharedPreferences.getInstance();
    settings = Settings(prefs);
    downloads = Downloads(prefs);
  });

  Future<void> shoot(
    WidgetTester tester,
    String name,
    Widget screen,
    Brightness brightness,
  ) async {
    // Tests draw shadows flat by default; real ones for screenshots
    debugDisableShadows = !screenshots;
    tester.view.physicalSize = phone * 3;
    tester.view.devicePixelRatio = 3;
    addTearDown(tester.view.reset);

    final key = GlobalKey();
    await tester.pumpWidget(
      RepaintBoundary(
        key: key,
        child: MaterialApp(
          debugShowCheckedModeBanner: false,
          theme: buildTheme(brightness),
          home: Scaffold(body: screen),
        ),
      ),
    );
    // A layout error (e.g. a row too wide) fails the test on its own
    await tester.pump(const Duration(milliseconds: 500));

    if (!screenshots) return;
    try {
      await _save(tester, key, name, brightness);
    } finally {
      debugDisableShadows = true; // as Flutter expects at the end of a test
    }
  }

  for (final brightness in Brightness.values) {
    testWidgets('Home, empty (${brightness.name})', (tester) async {
      await shoot(
        tester,
        'home-empty',
        HomeScreen(
          downloads: downloads,
          settings: settings,
          onGoToDownloads: () {},
        ),
        brightness,
      );
      expect(find.text('Paste'), findsOneWidget);
      expect(find.text('New Download'), findsOneWidget);
    });

    testWidgets('Home, link looked up (${brightness.name})', (tester) async {
      await shoot(
        tester,
        'home-preview',
        HomeScreen(
          downloads: downloads,
          settings: settings,
          onGoToDownloads: () {},
          preview: Map<String, dynamic>.from(preview),
        ),
        brightness,
      );
      expect(find.text('Download · 182 MB'), findsOneWidget);
      expect(find.text('1080p'), findsOneWidget);
      expect(find.text('182 MB'), findsWidgets);
    });

    testWidgets('Downloads (${brightness.name})', (tester) async {
      downloads.items.addAll(sampleItems());
      await shoot(
        tester,
        'downloads',
        DownloadsScreen(downloads: downloads, onGoHome: () {}),
        brightness,
      );
      expect(find.text('Downloading'), findsOneWidget);
      expect(find.byTooltip('Pause'), findsOneWidget);
      expect(find.byTooltip('Resume'), findsOneWidget);
      expect(find.text('This video is private.'), findsOneWidget);
      expect(find.text('Retry Failed'), findsOneWidget);
    });

    testWidgets('Downloads, empty (${brightness.name})', (tester) async {
      await shoot(
        tester,
        'downloads-empty',
        DownloadsScreen(downloads: downloads, onGoHome: () {}),
        brightness,
      );
      expect(find.text('No Downloads'), findsOneWidget);
    });

    testWidgets('Settings (${brightness.name})', (tester) async {
      await shoot(
        tester,
        'settings',
        SettingsScreen(settings: settings, downloads: downloads),
        brightness,
      );
      expect(find.text('Wi-Fi only'), findsOneWidget);
      expect(find.text('1.0.2'), findsOneWidget);
    });
  }
}

Future<void> _save(
  WidgetTester tester,
  GlobalKey key,
  String name,
  Brightness brightness,
) async {
  final boundary =
      key.currentContext!.findRenderObject()! as RenderRepaintBoundary;
  await tester.runAsync(() async {
    final image = await boundary.toImage(pixelRatio: 3);
    final png = await image.toByteData(format: ui.ImageByteFormat.png);
    final file = File('build/screenshots/$name-${brightness.name}.png');
    await file.create(recursive: true);
    await file.writeAsBytes(png!.buffer.asUint8List());
  });
}
