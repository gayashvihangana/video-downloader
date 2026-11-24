import 'dart:io';

import 'package:flutter/material.dart';
import 'package:package_info_plus/package_info_plus.dart';

import '../services/downloads.dart';
import '../services/python_api.dart';
import '../services/settings.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({
    super.key,
    required this.settings,
    required this.downloads,
  });

  final Settings settings;
  final Downloads downloads;

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  String _version = '';
  String _engine = '';

  @override
  void initState() {
    super.initState();
    _loadVersions();
  }

  Future<void> _loadVersions() async {
    final info = await PackageInfo.fromPlatform();
    if (mounted) setState(() => _version = info.version);
    try {
      final api = await PythonApi.instance;
      final versions = await api.call('/version');
      if (mounted) {
        setState(
          () => _engine =
              'yt-dlp ${versions['yt_dlp']} · Python ${versions['python']}',
        );
      }
    } catch (_) {
      if (mounted) setState(() => _engine = 'Starting…');
    }
  }

  @override
  Widget build(BuildContext context) {
    final settings = widget.settings;
    final text = Theme.of(context).textTheme;

    return SafeArea(
      child: ListenableBuilder(
        listenable: settings,
        builder: (context, _) => ListView(
          padding: const EdgeInsets.fromLTRB(16, 20, 16, 32),
          children: [
            Text('Settings', style: text.headlineMedium),
            const SizedBox(height: 16),
            const _Group('Downloads'),
            Card(
              child: Column(
                children: [
                  SwitchListTile(
                    secondary: const Icon(Icons.wifi_rounded),
                    title: const Text('Download on Wi-Fi only'),
                    subtitle: const Text('Saves your mobile data'),
                    value: settings.wifiOnly,
                    onChanged: (value) {
                      settings.wifiOnly = value;
                      widget.downloads.wifiOnly = value;
                    },
                  ),
                  ListTile(
                    leading: const Icon(Icons.photo_library_outlined),
                    title: const Text('Saved to'),
                    subtitle: Text(
                      Platform.isIOS
                          ? 'Videos: Photos  ·  Music: Files → Video Downloader'
                          : 'Movies / Music → Video Downloader',
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 16),
            const _Group('Appearance'),
            Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text('App theme'),
                    const SizedBox(height: 12),
                    SegmentedButton<ThemeMode>(
                      segments: const [
                        ButtonSegment(
                          value: ThemeMode.system,
                          label: Text('System'),
                          icon: Icon(Icons.brightness_auto_outlined),
                        ),
                        ButtonSegment(
                          value: ThemeMode.light,
                          label: Text('Light'),
                          icon: Icon(Icons.light_mode_outlined),
                        ),
                        ButtonSegment(
                          value: ThemeMode.dark,
                          label: Text('Dark'),
                          icon: Icon(Icons.dark_mode_outlined),
                        ),
                      ],
                      selected: {settings.themeMode},
                      onSelectionChanged: (value) =>
                          settings.themeMode = value.first,
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 16),
            const _Group('About'),
            Card(
              child: Column(
                children: [
                  ListTile(
                    leading: const Icon(Icons.info_outline_rounded),
                    title: const Text('Video Downloader'),
                    subtitle: Text(
                      [
                        if (_version.isNotEmpty) 'Version $_version',
                        if (_engine.isNotEmpty) _engine,
                      ].join('\n'),
                    ),
                  ),
                  ListTile(
                    leading: const Icon(Icons.gavel_outlined),
                    title: const Text('Licences'),
                    onTap: () => showLicensePage(
                      context: context,
                      applicationName: 'Video Downloader',
                      applicationVersion: _version,
                      applicationLegalese:
                          'Uses yt-dlp (Unlicense) and Python (PSF licence).',
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _Group extends StatelessWidget {
  const _Group(this.title);

  final String title;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(4, 0, 4, 8),
      child: Text(
        title,
        style: Theme.of(context).textTheme.titleSmall?.copyWith(
          color: Theme.of(context).colorScheme.primary,
        ),
      ),
    );
  }
}
