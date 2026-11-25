import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../models/download_item.dart';
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
    final hint = Platform.isAndroid
        ? 'Works with YouTube, TikTok, Instagram and Facebook. You can also '
              'share a link to this app.'
        : 'Works with YouTube, TikTok, Instagram and Facebook.';
    final backdrop = _look == _Look.ready
        ? (_info?['thumbnail_url'] as String?)
        : null;

    return Stack(
      children: [
        // The video's own colours behind everything once it's looked up
        AnimatedSwitcher(
          duration: const Duration(milliseconds: 400),
          child: backdrop == null
              ? const SizedBox.shrink()
              : Backdrop(key: ValueKey(backdrop), url: backdrop),
        ),
        SafeArea(
          bottom: false,
          child: ListView(
            padding: const EdgeInsets.only(bottom: 32),
            keyboardDismissBehavior: ScrollViewKeyboardDismissBehavior.onDrag,
            children: [
              const LargeTitle('New Download'),
              Section(
                footer: _look == _Look.empty ? hint : null,
                children: [
                  _LinkField(
                    controller: _link,
                    onChanged: _onChanged,
                    onPaste: _paste,
                    onClear: _clear,
                  ),
                ],
              ),
              AnimatedSwitcher(
                duration: const Duration(milliseconds: 220),
                child: KeyedSubtree(
                  key: ValueKey('$_look${_info?['url']}${_started != null}'),
                  child: _body(),
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _body() {
    switch (_look) {
      case _Look.loading:
        return const _LoadingPreview();
      case _Look.error:
        return _ErrorSection(message: _error, onRetry: _lookUp);
      case _Look.ready:
        return _preview();
      case _Look.empty:
        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            if (_started != null)
              Section(
                children: [
                  GroupRow(
                    title: 'Download started',
                    subtitle: _started,
                    value: 'View',
                    chevron: true,
                    onTap: widget.onGoToDownloads,
                  ),
                ],
              ),
            _RecentlySaved(downloads: widget.downloads),
          ],
        );
    }
  }

  Widget _preview() {
    final info = _info!;
    final p = context.palette;
    final text = Theme.of(context).textTheme;
    final duration = info['duration'] as num? ?? 0;
    final title = (info['title'] as String?)?.isNotEmpty == true
        ? info['title'] as String
        : 'Untitled';
    final details = [
      info['uploader'],
      info['site'],
      if (duration > 0) humanTime(duration),
    ].where((part) => part is String && part.isNotEmpty).join(' · ');
    final video = _mode == 'Video' && _hasVideo;
    final selected = video ? _qualities[_quality] : null;
    final size = selected == null ? '' : _sizeText(selected);

    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 4, 20, 0),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          AspectRatio(
            aspectRatio: 16 / 9,
            child: Artwork(
              url: info['thumbnail_url'] as String?,
              audio: !_hasVideo,
            ),
          ),
          const SizedBox(height: 18),
          Text(
            title,
            maxLines: 3,
            overflow: TextOverflow.ellipsis,
            style: text.titleLarge?.copyWith(
              fontSize: 22,
              fontWeight: FontWeight.w700,
              height: 1.2,
            ),
          ),
          if (details.isNotEmpty) ...[
            const SizedBox(height: 6),
            Text(
              details,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: text.bodyMedium?.copyWith(color: p.secondary),
            ),
          ],
          const SizedBox(height: 22),
          if (_hasVideo && _hasAudio) ...[
            PillToggle<String>(
              value: _mode,
              options: const {'Video': 'Video', 'Audio': 'Audio only'},
              onChanged: (value) => setState(() => _mode = value),
            ),
            const SizedBox(height: 18),
          ],
          if (video) ...[
            Text(
              'Quality',
              style: text.bodySmall?.copyWith(color: p.secondary),
            ),
            const SizedBox(height: 8),
            SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              clipBehavior: Clip.none,
              child: Row(
                children: [
                  for (var i = 0; i < _qualities.length; i++) ...[
                    if (i > 0) const SizedBox(width: 8),
                    QualityPill(
                      label: _qualities[i]['label'] as String,
                      size: _sizeText(_qualities[i]),
                      selected: i == _quality,
                      onTap: () => setState(() => _quality = i),
                    ),
                  ],
                ],
              ),
            ),
          ] else
            Text(
              'Best quality sound, saved as M4A. Plays on every phone.',
              style: text.bodyMedium?.copyWith(color: p.secondary),
            ),
          const SizedBox(height: 26),
          PrimaryButton(
            label: size.isEmpty ? 'Download' : 'Download · $size',
            icon: Icons.arrow_downward_rounded,
            onPressed: _download,
          ),
        ],
      ),
    );
  }
}

/// The last videos you saved, as artwork you can tap to open.
class _RecentlySaved extends StatelessWidget {
  const _RecentlySaved({required this.downloads});

  final Downloads downloads;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final text = Theme.of(context).textTheme;
    return ListenableBuilder(
      listenable: downloads,
      builder: (context, _) {
        final saved = downloads.recent
            .where((item) => item.state == DownloadState.done)
            .take(8)
            .toList();
        if (saved.isEmpty) return const SizedBox.shrink();
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 0, 20, 12),
              child: Text('Recently Saved', style: text.titleLarge),
            ),
            SizedBox(
              height: 160,
              child: ListView.separated(
                scrollDirection: Axis.horizontal,
                padding: const EdgeInsets.symmetric(horizontal: 20),
                itemCount: saved.length,
                separatorBuilder: (_, _) => const SizedBox(width: 12),
                itemBuilder: (context, i) {
                  final item = saved[i];
                  return GestureDetector(
                    onTap: () => downloads.open(item),
                    child: SizedBox(
                      width: 176,
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          SizedBox(
                            height: 99,
                            width: 176,
                            child: MediaThumb(
                              url: item.thumbnailUrl,
                              audio: item.audioOnly,
                              radius: 12,
                            ),
                          ),
                          const SizedBox(height: 8),
                          Text(
                            item.name,
                            maxLines: 2,
                            overflow: TextOverflow.ellipsis,
                            style: text.bodyMedium?.copyWith(
                              fontWeight: FontWeight.w600,
                              color: p.label,
                            ),
                          ),
                        ],
                      ),
                    ),
                  );
                },
              ),
            ),
          ],
        );
      },
    );
  }
}

// ---------- pieces of the screen ----------

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
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 4, 6, 4),
      child: Row(
        children: [
          Expanded(
            child: TextField(
              controller: controller,
              onChanged: onChanged,
              keyboardType: TextInputType.url,
              autocorrect: false,
              textInputAction: TextInputAction.go,
              style: Theme.of(context).textTheme.bodyLarge,
              decoration: const InputDecoration(hintText: 'Paste a link'),
            ),
          ),
          ListenableBuilder(
            listenable: controller,
            builder: (context, _) => controller.text.isEmpty
                ? TextButton(onPressed: onPaste, child: const Text('Paste'))
                : IconButton(
                    tooltip: 'Clear',
                    onPressed: onClear,
                    icon: Icon(Icons.cancel, color: p.tertiary, size: 20),
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
    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 4, 20, 0),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const AspectRatio(
            aspectRatio: 16 / 9,
            child: Pulse(height: double.infinity, radius: 16),
          ),
          const SizedBox(height: 18),
          const Pulse(height: 18),
          const SizedBox(height: 8),
          const Pulse(width: 180, height: 18),
          const SizedBox(height: 14),
          Text(
            'Looking up the link…',
            style: Theme.of(context).textTheme.bodyMedium
                ?.copyWith(color: p.secondary),
          ),
        ],
      ),
    );
  }
}

class _ErrorSection extends StatelessWidget {
  const _ErrorSection({required this.message, required this.onRetry});

  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final text = Theme.of(context).textTheme;
    return Section(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 13, 16, 13),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text("Couldn't open this link", style: text.titleMedium),
              const SizedBox(height: 3),
              Text(
                message,
                style: text.bodyMedium?.copyWith(color: p.secondary),
              ),
            ],
          ),
        ),
        GroupRow(title: 'Try Again', onTap: onRetry),
      ],
    );
  }
}
