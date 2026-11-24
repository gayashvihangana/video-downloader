import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../services/downloads.dart';
import '../services/python_api.dart';
import '../services/settings.dart';
import '../ui/theme.dart';
import '../ui/widgets.dart';
import '../util/format.dart';

/// Paste a link, see what it is, pick Video / Audio and quality, download.
class HomeScreen extends StatefulWidget {
  const HomeScreen({
    super.key,
    required this.downloads,
    required this.settings,
    required this.onGoToDownloads,
    this.sharedLink,
    @visibleForTesting this.preview,
  });

  final Downloads downloads;
  final Settings settings;
  final VoidCallback onGoToDownloads;
  final ValueNotifier<String?>? sharedLink;

  /// Show this looked-up link straight away (screenshots and tests).
  final Map<String, dynamic>? preview;

  @override
  State<HomeScreen> createState() => HomeScreenState();
}

enum _Look { empty, loading, ready, error }

class HomeScreenState extends State<HomeScreen> {
  final _link = TextEditingController();
  Timer? _settle;
  int _request = 0; // newer lookups win

  _Look _look = _Look.empty;
  Map<String, dynamic>? _info;
  String _error = '';
  String _mode = 'Video';
  int _quality = 0; // index into qualities
  String? _started; // "Download started: ..." message

  @override
  void initState() {
    super.initState();
    _mode = widget.settings.lastMode;
    widget.sharedLink?.addListener(_onShared);
    _onShared();
    final preview = widget.preview;
    if (preview != null) {
      _link.text = preview['url'] as String? ?? '';
      _show(preview);
    }
  }

  @override
  void dispose() {
    widget.sharedLink?.removeListener(_onShared);
    _settle?.cancel();
    _link.dispose();
    super.dispose();
  }

  void _onShared() {
    final link = widget.sharedLink?.value;
    if (link == null) return;
    widget.sharedLink!.value = null;
    _link.text = link;
    _onChanged(link, now: true);
  }

  // ---------- looking the link up ----------

  void _onChanged(String text, {bool now = false}) {
    _settle?.cancel();
    setState(() => _started = null);

    if (text.trim().isEmpty) {
      _request++;
      setState(() {
        _look = _Look.empty;
        _info = null;
      });
      return;
    }
    // Wait until typing / pasting settles
    _settle = Timer(
      now ? Duration.zero : const Duration(milliseconds: 600),
      _lookUp,
    );
  }

  Future<void> _lookUp() async {
    final url = _link.text.trim();
    final request = ++_request;
    setState(() => _look = _Look.loading);

    Map<String, dynamic> result;
    try {
      final api = await PythonApi.instance;
      result = await api.call('/lookup', body: {'url': url});
    } catch (error) {
      result = {
        'error': error is PythonApiException ? error.message : '$error',
      };
    }
    if (!mounted || request != _request) return; // a newer link was pasted
    setState(() => _show(result));
  }

  void _show(Map<String, dynamic> result) {
    final nothing =
        result['error'] == null &&
        (result['qualities'] as List? ?? const []).isEmpty &&
        result['has_audio_only'] != true;

    if (result['error'] != null || nothing) {
      _look = _Look.error;
      _error = nothing
          ? "This link has no video or sound a phone can save. Try the "
                'desktop app.'
          : result['error'] as String;
      _info = null;
    } else {
      _look = _Look.ready;
      _info = result;
      if (!_hasVideo) _mode = 'Audio';
      if (!_hasAudio) _mode = 'Video';
      _quality = _qualityIndex(widget.settings.lastQuality);
    }
  }

  List<Map<String, dynamic>> get _qualities => [
    for (final q in _info?['qualities'] as List? ?? const [])
      Map<String, dynamic>.from(q as Map),
  ];

  bool get _hasVideo => _qualities.isNotEmpty;

  /// Sound on its own is only offered if the site has it separately
  bool get _hasAudio => _info?['has_audio_only'] == true;

  /// Keep the quality picked last time if this video has it (by name).
  int _qualityIndex(String? name) {
    final index = _qualities.indexWhere((q) => q['label'] == name);
    return index < 0 ? 0 : index;
  }

  String _sizeText(Map<String, dynamic> q) {
    final size = humanSize(q['size'] as num?);
    if (size.isEmpty) return '';
    return '${q['approx'] == true ? '~' : ''}$size';
  }

  // ---------- buttons ----------

  Future<void> _paste() async {
    final data = await Clipboard.getData(Clipboard.kTextPlain);
    final text = data?.text?.trim() ?? '';
    if (text.isEmpty) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Copy a link first, then tap Paste.')),
      );
      return;
    }
    _link.text = text;
    _onChanged(text, now: true);
  }

  void _clear() {
    _link.clear();
    _onChanged('');
  }

  void _download() {
    final info = _info;
    if (info == null) return;

    final video = _mode == 'Video' && _hasVideo;
    final quality = video ? _qualities[_quality] : null;

    final item = widget.downloads.add(
      url: info['url'] as String,
      mode: video ? 'Video' : 'Audio',
      height: quality?['height'] as int?,
      quality: quality?['label'] as String? ?? '',
      title: info['title'] as String? ?? '',
      site: info['site'] as String? ?? '',
      thumbnailUrl: info['thumbnail_url'] as String?,
    );

    widget.settings.lastMode = _mode;
    if (quality != null) {
      widget.settings.lastQuality = quality['label'] as String;
    }

    // Ready for the next link
    _request++;
    _link.clear();
    FocusScope.of(context).unfocus();
    setState(() {
      _look = _Look.empty;
      _info = null;
      _started = item.name;
    });
  }

  // ---------- screen ----------

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      bottom: false,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(20, 12, 20, 32),
        keyboardDismissBehavior: ScrollViewKeyboardDismissBehavior.onDrag,
        children: [
          const _Brand(),
          const SizedBox(height: 28),
          const _Hero(),
          const SizedBox(height: 22),
          _LinkField(
            controller: _link,
            onChanged: _onChanged,
            onPaste: _paste,
            onClear: _clear,
          ),
          const SizedBox(height: 16),
          AnimatedSwitcher(
            duration: const Duration(milliseconds: 250),
            switchInCurve: Curves.easeOutCubic,
            transitionBuilder: (child, animation) => FadeTransition(
              opacity: animation,
              child: SlideTransition(
                position: Tween(
                  begin: const Offset(0, 0.03),
                  end: Offset.zero,
                ).animate(animation),
                child: child,
              ),
            ),
            child: KeyedSubtree(
              key: ValueKey('$_look${_info?['url']}${_started != null}'),
              child: _body(),
            ),
          ),
        ],
      ),
    );
  }

  Widget _body() {
    switch (_look) {
      case _Look.loading:
        return const _LoadingPreview();
      case _Look.error:
        return _ErrorCard(message: _error, onRetry: _lookUp);
      case _Look.ready:
        return _preview();
      case _Look.empty:
        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            if (_started != null) ...[
              _StartedCard(name: _started!, onOpen: widget.onGoToDownloads),
              const SizedBox(height: 16),
            ],
            const _Sites(),
            const SizedBox(height: 16),
            const _HowItWorks(),
          ],
        );
    }
  }

  Widget _preview() {
    final info = _info!;
    final p = context.palette;
    final text = Theme.of(context).textTheme;
    final duration = (info['duration'] as num? ?? 0);
    final site = info['site'] as String? ?? '';
    final uploader = info['uploader'] as String? ?? '';
    final title = (info['title'] as String?)?.isNotEmpty == true
        ? info['title'] as String
        : 'Untitled';
    final video = _mode == 'Video' && _hasVideo;
    final selected = video ? _qualities[_quality] : null;
    final size = selected == null ? '' : _sizeText(selected);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Panel(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Padding(
                padding: const EdgeInsets.all(8),
                child: AspectRatio(
                  aspectRatio: 16 / 9,
                  child: Stack(
                    fit: StackFit.expand,
                    children: [
                      MediaThumb(
                        url: info['thumbnail_url'] as String?,
                        audio: !_hasVideo,
                        radius: 16,
                      ),
                      if (site.isNotEmpty)
                        Positioned(
                          left: 10,
                          top: 10,
                          child: _Overlay(
                            child: Row(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                Container(
                                  width: 8,
                                  height: 8,
                                  decoration: BoxDecoration(
                                    color: siteColor(site, context),
                                    shape: BoxShape.circle,
                                  ),
                                ),
                                const SizedBox(width: 6),
                                Text(site),
                              ],
                            ),
                          ),
                        ),
                      if (duration > 0)
                        Positioned(
                          right: 10,
                          bottom: 10,
                          child: _Overlay(child: Text(humanTime(duration))),
                        ),
                    ],
                  ),
                ),
              ),
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 6, 16, 18),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      title,
                      style: text.titleMedium?.copyWith(height: 1.3),
                      maxLines: 3,
                      overflow: TextOverflow.ellipsis,
                    ),
                    if (uploader.isNotEmpty) ...[
                      const SizedBox(height: 6),
                      Row(
                        children: [
                          Icon(
                            Icons.account_circle_rounded,
                            size: 16,
                            color: p.muted,
                          ),
                          const SizedBox(width: 6),
                          Expanded(
                            child: Text(
                              uploader,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: text.bodyMedium?.copyWith(color: p.muted),
                            ),
                          ),
                        ],
                      ),
                    ],
                  ],
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 22),
        const SectionTitle('Save as'),
        Row(
          children: [
            Expanded(
              child: _FormatTile(
                icon: Icons.movie_rounded,
                title: 'Video',
                subtitle: _hasVideo
                    ? 'MP4 · up to ${_qualities.first['label']}'
                    : 'Not available',
                selected: _mode == 'Video',
                onTap: _hasVideo ? () => setState(() => _mode = 'Video') : null,
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: _FormatTile(
                icon: Icons.graphic_eq_rounded,
                title: 'Audio',
                subtitle: _hasAudio ? 'M4A · best sound' : 'Not available',
                selected: _mode == 'Audio',
                onTap: _hasAudio ? () => setState(() => _mode = 'Audio') : null,
              ),
            ),
          ],
        ),
        if (video) ...[
          const SizedBox(height: 22),
          const SectionTitle('Quality'),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              for (var i = 0; i < _qualities.length; i++)
                _QualityChip(
                  label: _qualities[i]['label'] as String,
                  size: _sizeText(_qualities[i]),
                  selected: i == _quality,
                  onTap: () => setState(() => _quality = i),
                ),
            ],
          ),
        ],
        const SizedBox(height: 26),
        GradientButton(
          label: 'Download',
          icon: Icons.download_rounded,
          trailing: size.isEmpty ? null : size,
          onPressed: _download,
        ),
      ],
    );
  }
}

// ---------- pieces of the screen ----------

class _Brand extends StatelessWidget {
  const _Brand();

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Row(
      children: [
        const AppLogo(size: 38),
        const SizedBox(width: 12),
        Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Video Downloader',
              style: Theme.of(context).textTheme.titleMedium,
            ),
            Text(
              'Videos & music, saved to your phone',
              style: TextStyle(color: p.muted, fontSize: 12.5),
            ),
          ],
        ),
      ],
    );
  }
}

class _Hero extends StatelessWidget {
  const _Hero();

  @override
  Widget build(BuildContext context) {
    final style = Theme.of(context).textTheme.displaySmall
        ?.copyWith(fontSize: 34);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('Paste a link,', style: style),
        GradientText('save it in seconds.', style: style),
      ],
    );
  }
}

class _LinkField extends StatelessWidget {
  const _LinkField({
    required this.controller,
    required this.onChanged,
    required this.onPaste,
    required this.onClear,
  });

  final TextEditingController controller;
  final ValueChanged<String> onChanged;
  final VoidCallback onPaste;
  final VoidCallback onClear;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Container(
      padding: const EdgeInsets.fromLTRB(16, 6, 6, 6),
      decoration: BoxDecoration(
        color: p.card,
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: p.border),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withValues(alpha: 0.04),
            blurRadius: 18,
            offset: const Offset(0, 6),
          ),
        ],
      ),
      child: Row(
        children: [
          Icon(Icons.link_rounded, color: p.muted),
          const SizedBox(width: 10),
          Expanded(
            child: TextField(
              controller: controller,
              onChanged: onChanged,
              keyboardType: TextInputType.url,
              autocorrect: false,
              textInputAction: TextInputAction.go,
              style: const TextStyle(
                fontSize: 15.5,
                fontWeight: FontWeight.w500,
              ),
              decoration: const InputDecoration(
                hintText: 'Paste a video link',
                isDense: true,
              ),
            ),
          ),
          ListenableBuilder(
            listenable: controller,
            builder: (context, _) => controller.text.isEmpty
                ? FilledButton.icon(
                    onPressed: onPaste,
                    icon: const Icon(Icons.content_paste_rounded, size: 18),
                    label: const Text('Paste'),
                    style: FilledButton.styleFrom(
                      backgroundColor: AppPalette.accent,
                      minimumSize: const Size(0, 44),
                      padding: const EdgeInsets.symmetric(horizontal: 16),
                      shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(15),
                      ),
                    ),
                  )
                : IconButton(
                    tooltip: 'Clear',
                    onPressed: onClear,
                    icon: const Icon(Icons.cancel_rounded),
                  ),
          ),
        ],
      ),
    );
  }
}

class _Sites extends StatelessWidget {
  const _Sites();

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Wrap(
      spacing: 8,
      runSpacing: 8,
      children: [
        for (final site in ['YouTube', 'TikTok', 'Instagram', 'Facebook'])
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
            decoration: BoxDecoration(
              color: p.card,
              borderRadius: BorderRadius.circular(20),
              border: Border.all(color: p.border),
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Container(
                  width: 8,
                  height: 8,
                  decoration: BoxDecoration(
                    color: siteColor(site, context),
                    shape: BoxShape.circle,
                  ),
                ),
                const SizedBox(width: 8),
                Text(
                  site,
                  style: const TextStyle(
                    fontSize: 13,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ],
            ),
          ),
      ],
    );
  }
}

class _HowItWorks extends StatelessWidget {
  const _HowItWorks();

  @override
  Widget build(BuildContext context) {
    final share = Platform.isAndroid
        ? 'Or share it straight from the app to Video Downloader.'
        : 'In the app, tap Share › Copy link.';
    final steps = [
      (Icons.content_copy_rounded, 'Copy a link', share),
      (
        Icons.tune_rounded,
        'Pick video or audio',
        'Choose the quality you want.',
      ),
      (
        Icons.photo_library_rounded,
        'It’s in your gallery',
        'Even if you leave the app.',
      ),
    ];
    final p = context.palette;
    return Panel(
      padding: const EdgeInsets.fromLTRB(18, 18, 18, 6),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('How it works', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 14),
          for (final (i, step) in steps.indexed)
            Padding(
              padding: const EdgeInsets.only(bottom: 14),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Container(
                    width: 30,
                    height: 30,
                    alignment: Alignment.center,
                    decoration: BoxDecoration(
                      gradient: AppPalette.gradient,
                      borderRadius: BorderRadius.circular(10),
                    ),
                    child: Text(
                      '${i + 1}',
                      style: const TextStyle(
                        color: Colors.white,
                        fontWeight: FontWeight.w800,
                      ),
                    ),
                  ),
                  const SizedBox(width: 14),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          step.$2,
                          style: const TextStyle(
                            fontWeight: FontWeight.w700,
                            fontSize: 15,
                          ),
                        ),
                        const SizedBox(height: 2),
                        Text(
                          step.$3,
                          style: TextStyle(color: p.muted, fontSize: 13.5),
                        ),
                      ],
                    ),
                  ),
                  Icon(step.$1, size: 20, color: p.muted),
                ],
              ),
            ),
        ],
      ),
    );
  }
}

class _LoadingPreview extends StatelessWidget {
  const _LoadingPreview();

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Panel(
      padding: const EdgeInsets.all(8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const AspectRatio(
            aspectRatio: 16 / 9,
            child: Pulse(height: double.infinity, radius: 16),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(8, 16, 8, 10),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Pulse(height: 16),
                const SizedBox(height: 8),
                const Pulse(width: 180, height: 16),
                const SizedBox(height: 16),
                Row(
                  children: [
                    const SizedBox(
                      width: 16,
                      height: 16,
                      child: CircularProgressIndicator(strokeWidth: 2.2),
                    ),
                    const SizedBox(width: 10),
                    Text(
                      'Looking up the link…',
                      style: TextStyle(
                        color: p.muted,
                        fontWeight: FontWeight.w500,
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _ErrorCard extends StatelessWidget {
  const _ErrorCard({required this.message, required this.onRetry});

  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final error = Theme.of(context).colorScheme.error;
    return Panel(
      padding: const EdgeInsets.all(18),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          IconSquare(Icons.link_off_rounded, color: error),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  "Couldn't open this link",
                  style: TextStyle(fontWeight: FontWeight.w700, fontSize: 15.5),
                ),
                const SizedBox(height: 4),
                Text(message, style: TextStyle(color: p.muted, height: 1.35)),
                const SizedBox(height: 8),
                TextButton.icon(
                  onPressed: onRetry,
                  icon: const Icon(Icons.refresh_rounded, size: 18),
                  label: const Text('Try again'),
                  style: TextButton.styleFrom(
                    padding: const EdgeInsets.symmetric(horizontal: 8),
                    visualDensity: VisualDensity.compact,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _StartedCard extends StatelessWidget {
  const _StartedCard({required this.name, required this.onOpen});

  final String name;
  final VoidCallback onOpen;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Panel(
      onTap: onOpen,
      padding: const EdgeInsets.fromLTRB(14, 14, 10, 14),
      child: Row(
        children: [
          IconSquare(Icons.check_rounded, color: p.success),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  'Download started',
                  style: TextStyle(fontWeight: FontWeight.w700, fontSize: 15),
                ),
                const SizedBox(height: 2),
                Text(
                  name,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(color: p.muted, fontSize: 13.5),
                ),
              ],
            ),
          ),
          const SizedBox(width: 8),
          Text(
            'View',
            style: TextStyle(
              color: AppPalette.accent,
              fontWeight: FontWeight.w700,
            ),
          ),
          const Icon(Icons.chevron_right_rounded, color: AppPalette.accent),
        ],
      ),
    );
  }
}

/// Dark glass label over the picture ("YouTube", "3:12").
class _Overlay extends StatelessWidget {
  const _Overlay({required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 5),
      decoration: BoxDecoration(
        color: Colors.black.withValues(alpha: 0.62),
        borderRadius: BorderRadius.circular(10),
      ),
      child: DefaultTextStyle.merge(
        style: const TextStyle(
          color: Colors.white,
          fontSize: 12,
          fontWeight: FontWeight.w700,
        ),
        child: child,
      ),
    );
  }
}

class _FormatTile extends StatelessWidget {
  const _FormatTile({
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.selected,
    required this.onTap,
  });

  final IconData icon;
  final String title;
  final String subtitle;
  final bool selected;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final enabled = onTap != null;
    return Semantics(
      selected: selected,
      button: true,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 200),
        decoration: BoxDecoration(
          color: selected ? AppPalette.accent.withValues(alpha: 0.08) : p.card,
          borderRadius: BorderRadius.circular(18),
          border: Border.all(
            color: selected ? AppPalette.accent : p.border,
            width: selected ? 1.6 : 1,
          ),
        ),
        child: Material(
          type: MaterialType.transparency,
          child: InkWell(
            onTap: onTap,
            borderRadius: BorderRadius.circular(18),
            child: Opacity(
              opacity: enabled ? 1 : 0.45,
              child: Padding(
                padding: const EdgeInsets.all(14),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Container(
                          width: 36,
                          height: 36,
                          decoration: BoxDecoration(
                            gradient: selected ? AppPalette.gradient : null,
                            color: selected ? null : p.cardAlt,
                            borderRadius: BorderRadius.circular(11),
                          ),
                          child: Icon(
                            icon,
                            size: 20,
                            color: selected ? Colors.white : p.muted,
                          ),
                        ),
                        const Spacer(),
                        Icon(
                          selected
                              ? Icons.radio_button_checked_rounded
                              : Icons.radio_button_unchecked_rounded,
                          size: 20,
                          color: selected ? AppPalette.accent : p.border,
                        ),
                      ],
                    ),
                    const SizedBox(height: 12),
                    Text(
                      title,
                      style: const TextStyle(
                        fontWeight: FontWeight.w700,
                        fontSize: 16,
                      ),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      subtitle,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(color: p.muted, fontSize: 12.5),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _QualityChip extends StatelessWidget {
  const _QualityChip({
    required this.label,
    required this.size,
    required this.selected,
    required this.onTap,
  });

  final String label;
  final String size;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Semantics(
      selected: selected,
      button: true,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 200),
        decoration: BoxDecoration(
          color: selected ? AppPalette.accent : p.card,
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: selected ? AppPalette.accent : p.border),
        ),
        child: Material(
          type: MaterialType.transparency,
          child: InkWell(
            onTap: onTap,
            borderRadius: BorderRadius.circular(14),
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
              child: Column(
                children: [
                  Text(
                    label,
                    style: TextStyle(
                      fontWeight: FontWeight.w800,
                      fontSize: 15,
                      color: selected ? Colors.white : p.text,
                    ),
                  ),
                  if (size.isNotEmpty)
                    Text(
                      size,
                      style: TextStyle(
                        fontSize: 11.5,
                        fontWeight: FontWeight.w600,
                        color: selected ? Colors.white70 : p.muted,
                      ),
                    ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
