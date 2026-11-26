import 'dart:io';

import 'package:flutter/material.dart';
import 'package:package_info_plus/package_info_plus.dart';

import '../services/account.dart';
import '../services/downloads.dart';
import '../services/python_api.dart';
import '../services/settings.dart';
import '../ui/theme.dart';
import '../ui/widgets.dart';
import 'sign_in.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({
    super.key,
    required this.settings,
    required this.downloads,
    this.account,
  });

  final Settings settings;
  final Downloads downloads;
  final Account? account; // null in screen tests

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
        listenable: Listenable.merge([settings, widget.account]),
        builder: (context, _) => ListView(
          padding: const EdgeInsets.only(bottom: 32),
          children: [
            const LargeTitle('Settings'),
            if (widget.account case final account?) _youtube(account),
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

  Widget _youtube(Account account) {
    final p = context.palette;
    if (account.signedIn) {
      return Section(
        header: 'YouTube',
        footer:
            'Your login stays on this phone. It lets the app through '
            "YouTube's \"not a robot\" check.",
        children: [
          GroupRow(
            title: 'Signed in',
            trailing: Icon(
              Icons.check_circle_rounded,
              color: p.green,
              size: 22,
            ),
          ),
          GroupRow(
            title: 'Sign Out',
            destructive: true,
            onTap: account.signOut,
          ),
        ],
      );
    }
    return Section(
      header: 'YouTube',
      footer:
          "If YouTube asks you to confirm you're not a robot, sign in. "
          'Your login stays on this phone.',
      children: [
        GroupRow(
          title: 'Sign in to YouTube',
          chevron: true,
          onTap: () async {
            final messenger = ScaffoldMessenger.of(context);
            final ok = await Navigator.push<bool>(
              context,
              MaterialPageRoute(
                fullscreenDialog: true,
                builder: (_) => SignInScreen(account: account),
              ),
            );
            if (ok == true) {
              messenger.showSnackBar(
                const SnackBar(content: Text('Signed in to YouTube.')),
              );
            }
          },
        ),
      ],
    );
  }
}
