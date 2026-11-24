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
      // not worth an error: the line just stays empty
    }
    try {
      final api = await PythonApi.instance;
      final versions = await api.call('/version');
      if (mounted) {
        setState(
          () => _engine =
              'yt-dlp ${versions['yt_dlp']}  ·  Python ${versions['python']}',
        );
      }
    } catch (_) {
      if (mounted) setState(() => _engine = 'Download engine starting…');
    }
  }

  @override
  Widget build(BuildContext context) {
    final settings = widget.settings;
    final p = context.palette;

    return SafeArea(
      bottom: false,
      child: ListenableBuilder(
        listenable: settings,
        builder: (context, _) => ListView(
          padding: const EdgeInsets.fromLTRB(20, 20, 20, 32),
          children: [
            const PageHeader('Settings'),
            _AboutCard(version: _version, engine: _engine),
            const SizedBox(height: 26),
            const SectionTitle('Downloads'),
            Panel(
              child: Column(
                children: [
                  SwitchListTile(
                    secondary: const IconSquare(
                      Icons.wifi_rounded,
                      color: Color(0xFF2F80ED),
                    ),
                    title: const Text('Wi-Fi only'),
                    subtitle: const Text('Wait for Wi-Fi to save mobile data'),
                    value: settings.wifiOnly,
                    onChanged: (value) {
                      settings.wifiOnly = value;
                      widget.downloads.wifiOnly = value;
                    },
                  ),
                  Divider(indent: 70, color: p.border),
                  ListTile(
                    leading: IconSquare(
                      Icons.photo_library_rounded,
                      color: p.success,
                    ),
                    title: const Text('Saved to'),
                    subtitle: Text(
                      Platform.isIOS
                          ? 'Videos in Photos  ·  Music in Files'
                          : 'Movies and Music › Video Downloader',
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 26),
            const SectionTitle('Appearance'),
            Row(
              children: [
                for (final (mode, label) in const [
                  (ThemeMode.system, 'Auto'),
                  (ThemeMode.light, 'Light'),
                  (ThemeMode.dark, 'Dark'),
                ]) ...[
                  if (mode != ThemeMode.system) const SizedBox(width: 10),
                  Expanded(
                    child: _ThemeChoice(
                      mode: mode,
                      label: label,
                      selected: settings.themeMode == mode,
                      onTap: () => settings.themeMode = mode,
                    ),
                  ),
                ],
              ],
            ),
            const SizedBox(height: 26),
            const SectionTitle('About'),
            Panel(
              child: ListTile(
                leading: const IconSquare(
                  Icons.description_rounded,
                  color: Color(0xFF8E59FF),
                ),
                title: const Text('Licences'),
                subtitle: const Text('yt-dlp, Python and the app’s parts'),
                trailing: Icon(Icons.chevron_right_rounded, color: p.muted),
                onTap: () => showLicensePage(
                  context: context,
                  applicationName: 'Video Downloader',
                  applicationVersion: _version,
                  applicationIcon: const Padding(
                    padding: EdgeInsets.all(12),
                    child: AppLogo(size: 56),
                  ),
                  applicationLegalese:
                      'Uses yt-dlp (Unlicense) and Python (PSF licence).',
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _AboutCard extends StatelessWidget {
  const _AboutCard({required this.version, required this.engine});

  final String version;
  final String engine;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Container(
      padding: const EdgeInsets.all(18),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(radius),
        gradient: LinearGradient(
          colors: [
            AppPalette.accent.withValues(alpha: 0.16),
            AppPalette.accentEnd.withValues(alpha: 0.08),
          ],
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
        ),
        border: Border.all(color: AppPalette.accent.withValues(alpha: 0.18)),
      ),
      child: Row(
        children: [
          const AppLogo(size: 54),
          const SizedBox(width: 16),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'Video Downloader',
                  style: Theme.of(context).textTheme.titleLarge,
                ),
                const SizedBox(height: 2),
                Text(
                  version.isEmpty ? ' ' : 'Version $version',
                  style: TextStyle(color: p.text, fontWeight: FontWeight.w600),
                ),
                if (engine.isNotEmpty)
                  Text(
                    engine,
                    style: TextStyle(color: p.muted, fontSize: 12.5),
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// A tiny picture of the app in that theme.
class _ThemeChoice extends StatelessWidget {
  const _ThemeChoice({
    required this.mode,
    required this.label,
    required this.selected,
    required this.onTap,
  });

  final ThemeMode mode;
  final String label;
  final bool selected;
  final VoidCallback onTap;

  Widget _mini(AppPalette look) => Container(
    color: look.background,
    padding: const EdgeInsets.all(7),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Container(
          height: 7,
          width: 34,
          decoration: BoxDecoration(
            color: look.text,
            borderRadius: BorderRadius.circular(4),
          ),
        ),
        const SizedBox(height: 6),
        Expanded(
          child: Container(
            decoration: BoxDecoration(
              color: look.card,
              borderRadius: BorderRadius.circular(6),
              border: Border.all(color: look.border),
            ),
          ),
        ),
        const SizedBox(height: 6),
        Container(
          height: 9,
          decoration: BoxDecoration(
            gradient: AppPalette.gradient,
            borderRadius: BorderRadius.circular(4),
          ),
        ),
      ],
    ),
  );

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final picture = switch (mode) {
      ThemeMode.light => _mini(AppPalette.light),
      ThemeMode.dark => _mini(AppPalette.dark),
      ThemeMode.system => Row(
        children: [
          Expanded(child: ClipRect(child: _mini(AppPalette.light))),
          Expanded(child: ClipRect(child: _mini(AppPalette.dark))),
        ],
      ),
    };

    return Semantics(
      selected: selected,
      button: true,
      label: '$label theme',
      child: GestureDetector(
        onTap: onTap,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 200),
          padding: const EdgeInsets.all(6),
          decoration: BoxDecoration(
            color: p.card,
            borderRadius: BorderRadius.circular(18),
            border: Border.all(
              color: selected ? AppPalette.accent : p.border,
              width: selected ? 2 : 1,
            ),
          ),
          child: Column(
            children: [
              ClipRRect(
                borderRadius: BorderRadius.circular(12),
                child: SizedBox(height: 78, child: picture),
              ),
              const SizedBox(height: 8),
              Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  if (selected) ...[
                    const Icon(
                      Icons.check_circle_rounded,
                      size: 16,
                      color: AppPalette.accent,
                    ),
                    const SizedBox(width: 4),
                  ],
                  Text(
                    label,
                    style: TextStyle(
                      fontWeight: FontWeight.w700,
                      color: selected ? AppPalette.accent : p.text,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 4),
            ],
          ),
        ),
      ),
    );
  }
}
