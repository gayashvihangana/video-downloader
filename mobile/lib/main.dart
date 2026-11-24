import 'dart:async';
import 'dart:io';

import 'package:background_downloader/background_downloader.dart';
import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'screens/downloads.dart';
import 'screens/home.dart';
import 'screens/settings.dart';
import 'services/downloads.dart';
import 'services/python_api.dart';
import 'services/settings.dart';
import 'services/share.dart';
import 'ui/theme.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  final prefs = await SharedPreferences.getInstance();
  final settings = Settings(prefs);
  final downloads = Downloads(prefs)..wifiOnly = settings.wifiOnly;
  await downloads.start();

  // Start the download engine now, so the first link is quick
  unawaited(PythonApi.instance.then((_) {}, onError: (_) {}));

  runApp(VideoDownloaderApp(settings: settings, downloads: downloads));
}

class VideoDownloaderApp extends StatelessWidget {
  const VideoDownloaderApp({
    super.key,
    required this.settings,
    required this.downloads,
  });

  final Settings settings;
  final Downloads downloads;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: settings,
      builder: (context, _) => MaterialApp(
        title: 'Video Downloader',
        debugShowCheckedModeBanner: false,
        theme: buildTheme(Brightness.light),
        darkTheme: buildTheme(Brightness.dark),
        themeMode: settings.themeMode,
        home: AppShell(settings: settings, downloads: downloads),
      ),
    );
  }
}

/// Home · Downloads · Settings (like the desktop app's sidebar)
class AppShell extends StatefulWidget {
  const AppShell({super.key, required this.settings, required this.downloads});

  final Settings settings;
  final Downloads downloads;

  @override
  State<AppShell> createState() => _AppShellState();
}

class _AppShellState extends State<AppShell> {
  int _page = 0;
  final _sharedLink = ValueNotifier<String?>(null);

  @override
  void initState() {
    super.initState();

    // "Share -> Video Downloader": open Home with the link
    ShareReceiver.listen((link) {
      _sharedLink.value = link;
      setState(() => _page = 0);
    });

    // Android 13+: allow the progress notification (asked once)
    if (Platform.isAndroid) {
      unawaited(
        FileDownloader().permissions.request(PermissionType.notifications),
      );
    }
  }

  @override
  void dispose() {
    _sharedLink.dispose();
    super.dispose();
  }

  void _go(int page) => setState(() => _page = page);

  @override
  Widget build(BuildContext context) {
    final pages = [
      HomeScreen(
        downloads: widget.downloads,
        settings: widget.settings,
        sharedLink: _sharedLink,
        onGoToDownloads: () => _go(1),
      ),
      DownloadsScreen(downloads: widget.downloads, onGoHome: () => _go(0)),
      SettingsScreen(settings: widget.settings, downloads: widget.downloads),
    ];

    return Scaffold(
      // Keep each page's state (typed link, scroll) when switching tabs
      body: IndexedStack(index: _page, children: pages),
      bottomNavigationBar: ListenableBuilder(
        listenable: widget.downloads,
        builder: (context, _) {
          final running = widget.downloads.current.length;
          return DecoratedBox(
            decoration: BoxDecoration(
              border: Border(
                top: BorderSide(color: context.palette.separator, width: 0.5),
              ),
            ),
            child: NavigationBar(
              selectedIndex: _page,
              onDestinationSelected: _go,
              destinations: [
                const NavigationDestination(
                  icon: Icon(Icons.add_circle_outline_rounded),
                  selectedIcon: Icon(Icons.add_circle_rounded),
                  label: 'New',
                ),
                NavigationDestination(
                  icon: Badge(
                    isLabelVisible: running > 0,
                    label: Text('$running'),
                    child: const Icon(Icons.download_for_offline_outlined),
                  ),
                  selectedIcon: Badge(
                    isLabelVisible: running > 0,
                    label: Text('$running'),
                    child: const Icon(Icons.download_for_offline_rounded),
                  ),
                  label: 'Downloads',
                ),
                const NavigationDestination(
                  icon: Icon(Icons.settings_outlined),
                  selectedIcon: Icon(Icons.settings_rounded),
                  label: 'Settings',
                ),
              ],
            ),
          );
        },
      ),
    );
  }
}
