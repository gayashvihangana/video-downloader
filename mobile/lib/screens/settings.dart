import 'dart:io';

import 'package:flutter/material.dart';
import 'package:package_info_plus/package_info_plus.dart';

import '../services/downloads.dart';
import '../services/python_api.dart';
import '../services/settings.dart';
import '../ui/theme.dart';
import '../ui/widgets.dart';

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
    try {
      final info = await PackageInfo.fromPlatform();
      if (mounted) setState(() => _version = info.version);
    } catch (_) {
      // not worth an error: the row just stays empty
    }
    try {
      final api = await PythonApi.instance;
      final versions = await api.call('/version');
      if (mounted) setState(() => _engine = 'yt-dlp ${versions['yt_dlp']}');
    } catch (_) {
      if (mounted) setState(() => _engine = 'Starting…');
    }
  }

  @override
  Widget build(BuildContext context) {
    final settings = widget.settings;
    final p = context.palette;

    Widget check(bool on) => SizedBox(
      width: 22,
      child: on ? Icon(Icons.check_rounded, color: p.label, size: 22) : null,
    );

    return SafeArea(
      bottom: false,
      child: ListenableBuilder(
        listenable: settings,
        builder: (context, _) => ListView(
          padding: const EdgeInsets.only(bottom: 32),
          children: [
            const LargeTitle('Settings'),
            Section(
              header: 'Downloads',
              footer: Platform.isIOS
                  ? 'Videos are saved to Photos, music to Files › Video '
                        'Downloader.'
                  : 'Saved to Movies and Music › Video Downloader.',
              children: [
                GroupRow(
                  title: 'Wi-Fi only',
                  subtitle: 'Wait for Wi-Fi to save mobile data',
                  trailing: Switch.adaptive(
                    value: settings.wifiOnly,
                    activeTrackColor: p.green,
                    onChanged: (value) {
                      settings.wifiOnly = value;
                      widget.downloads.wifiOnly = value;
                    },
                  ),
                ),
              ],
            ),
            Section(
              header: 'Appearance',
              children: [
                for (final (mode, label) in const [
                  (ThemeMode.system, 'Automatic'),
                  (ThemeMode.light, 'Light'),
                  (ThemeMode.dark, 'Dark'),
                ])
                  GroupRow(
                    title: label,
                    trailing: check(settings.themeMode == mode),
                    onTap: () => settings.themeMode = mode,
                  ),
              ],
            ),
            Section(
              header: 'About',
              footer:
                  'Video Downloader uses yt-dlp to find videos. Only download '
                  "what you're allowed to.",
              children: [
                GroupRow(title: 'Version', value: _version),
                GroupRow(title: 'Download engine', value: _engine),
                GroupRow(
                  title: 'Licences',
                  chevron: true,
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
          ],
        ),
      ),
    );
  }
}
