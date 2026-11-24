import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../services/downloads.dart';
import '../services/python_api.dart';
import '../services/settings.dart';
import '../util/format.dart';

/// Paste a link, see what it is, pick Video / Audio and quality, download.
class HomeScreen extends StatefulWidget {
  const HomeScreen({
    super.key,
    required this.downloads,
    required this.settings,
    required this.onGoToDownloads,
    this.sharedLink,
  });

  final Downloads downloads;
  final Settings settings;
  final VoidCallback onGoToDownloads;
  final ValueNotifier<String?>? sharedLink;

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
    _settle = Timer(now ? Duration.zero : const Duration(milliseconds: 600), _lookUp);
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
      result = {'error': error is PythonApiException ? error.message : '$error'};
    }
    if (!mounted || request != _request) return; // a newer link was pasted

    setState(() {
      final nothing = result['error'] == null &&
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
    });
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

  String _qualityText(Map<String, dynamic> q) {
    final size = humanSize(q['size'] as num?);
    if (size.isEmpty) return q['label'] as String;
    return '${q['label']}  ·  ${q['approx'] == true ? '~' : ''}$size';
  }

  // ---------- buttons ----------

  Future<void> _paste() async {
    final data = await Clipboard.getData(Clipboard.kTextPlain);
    final text = data?.text?.trim() ?? '';
    if (text.isEmpty) return;
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
    if (quality != null) widget.settings.lastQuality = quality['label'] as String;

    // Ready for the next link
    _request++;
    _link.clear();
    setState(() {
      _look = _Look.empty;
      _info = null;
      _started = item.name;
    });
  }

  // ---------- screen ----------

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;

    return SafeArea(
      child: ListView(
        padding: const EdgeInsets.fromLTRB(16, 20, 16, 32),
        children: [
          Text('New download', style: text.headlineMedium),
          const SizedBox(height: 4),
          Text(
            'Videos and music from YouTube, TikTok, Instagram and Facebook.',
            style: text.bodyMedium?.copyWith(
              color: Theme.of(context).colorScheme.onSurfaceVariant,
            ),
          ),
          const SizedBox(height: 20),
          _linkCard(),
          const SizedBox(height: 12),
          if (_look != _Look.empty) _previewCard(),
          if (_look == _Look.ready) ...[
            const SizedBox(height: 16),
            FilledButton.icon(
              onPressed: _download,
              icon: const Icon(Icons.download_rounded),
              label: const Text('Download'),
              style: FilledButton.styleFrom(
                minimumSize: const Size.fromHeight(52),
                textStyle: text.titleMedium,
              ),
            ),
          ],
          if (_started != null) ...[
            const SizedBox(height: 12),
            _startedCard(),
          ],
        ],
      ),
    );
  }

  Widget _linkCard() {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          children: [
            TextField(
              controller: _link,
              onChanged: _onChanged,
              keyboardType: TextInputType.url,
              autocorrect: false,
              decoration: const InputDecoration(
                hintText: 'Paste a link here',
                prefixIcon: Icon(Icons.link_rounded),
                border: OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 8),
            Row(
              children: [
                FilledButton.tonalIcon(
                  onPressed: _paste,
                  icon: const Icon(Icons.content_paste_rounded),
                  label: const Text('Paste'),
                ),
                const SizedBox(width: 8),
                TextButton.icon(
                  onPressed: _clear,
                  icon: const Icon(Icons.clear_rounded),
                  label: const Text('Clear'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _previewCard() {
    final scheme = Theme.of(context).colorScheme;
    final text = Theme.of(context).textTheme;
    final info = _info;

    if (_look == _Look.loading) {
      return const Card(
        child: Padding(
          padding: EdgeInsets.all(20),
          child: Row(
            children: [
              SizedBox(
                width: 22,
                height: 22,
                child: CircularProgressIndicator(strokeWidth: 2.5),
              ),
              SizedBox(width: 16),
              Text('Looking up the link…'),
            ],
          ),
        ),
      );
    }

    if (_look == _Look.error || info == null) {
      return Card(
        color: scheme.errorContainer,
        child: ListTile(
          leading: Icon(Icons.error_outline_rounded, color: scheme.onErrorContainer),
          title: Text(
            "Couldn't open this link",
            style: TextStyle(color: scheme.onErrorContainer),
          ),
          subtitle: Text(_error, style: TextStyle(color: scheme.onErrorContainer)),
          trailing: IconButton(
            tooltip: 'Try again',
            onPressed: _lookUp,
            icon: Icon(Icons.refresh_rounded, color: scheme.onErrorContainer),
          ),
        ),
      );
    }

    final meta = [
      info['site'],
      info['uploader'],
      if ((info['duration'] as num? ?? 0) > 0) humanTime(info['duration'] as num),
    ].where((part) => part != null && '$part'.isNotEmpty).join('  ·  ');

    return Card(
      clipBehavior: Clip.antiAlias,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (info['thumbnail_url'] != null)
            AspectRatio(
              aspectRatio: 16 / 9,
              child: Container(
                color: scheme.surfaceContainerHighest,
                child: Image.network(
                  info['thumbnail_url'] as String,
                  fit: BoxFit.contain,
                  errorBuilder: (_, _, _) =>
                      const Icon(Icons.movie_outlined, size: 48),
                ),
              ),
            ),
          Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  (info['title'] as String?)?.isNotEmpty == true
                      ? info['title'] as String
                      : 'Untitled',
                  style: text.titleMedium,
                  maxLines: 3,
                  overflow: TextOverflow.ellipsis,
                ),
                if (meta.isNotEmpty) ...[
                  const SizedBox(height: 4),
                  Text(
                    meta,
                    style: text.bodySmall?.copyWith(
                      color: scheme.onSurfaceVariant,
                    ),
                  ),
                ],
                const SizedBox(height: 16),
                SegmentedButton<String>(
                  segments: [
                    ButtonSegment(
                      value: 'Video',
                      icon: const Icon(Icons.movie_outlined),
                      label: const Text('Video'),
                      enabled: _hasVideo,
                    ),
                    ButtonSegment(
                      value: 'Audio',
                      icon: const Icon(Icons.music_note_outlined),
                      label: const Text('Audio'),
                      enabled: _hasAudio,
                    ),
                  ],
                  selected: {_mode},
                  onSelectionChanged: (value) =>
                      setState(() => _mode = value.first),
                ),
                const SizedBox(height: 12),
                if (_mode == 'Video' && _hasVideo)
                  DropdownMenu<int>(
                    key: ValueKey(info['url']),
                    initialSelection: _quality,
                    label: const Text('Quality'),
                    expandedInsets: EdgeInsets.zero,
                    onSelected: (value) =>
                        setState(() => _quality = value ?? 0),
                    dropdownMenuEntries: [
                      for (var i = 0; i < _qualities.length; i++)
                        DropdownMenuEntry(
                          value: i,
                          label: _qualityText(_qualities[i]),
                        ),
                    ],
                  )
                else
                  Text(
                    'Best quality sound (M4A, plays on every phone)',
                    style: text.bodyMedium,
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _startedCard() {
    final scheme = Theme.of(context).colorScheme;
    return Card(
      color: scheme.secondaryContainer,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 12, 8, 12),
        child: Row(
          children: [
            Icon(Icons.check_circle_rounded, color: scheme.onSecondaryContainer),
            const SizedBox(width: 12),
            Expanded(
              child: Text(
                'Download started: $_started',
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(color: scheme.onSecondaryContainer),
              ),
            ),
            TextButton(
              onPressed: widget.onGoToDownloads,
              child: const Text('Go to Downloads'),
            ),
          ],
        ),
      ),
    );
  }
}
