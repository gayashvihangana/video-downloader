import 'dart:async';
import 'dart:io';

import 'package:flutter/cupertino.dart';
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
    final hint = Platform.isAndroid
        ? 'Works with YouTube, TikTok, Instagram and Facebook. You can also '
              'share a link to this app.'
        : 'Works with YouTube, TikTok, Instagram and Facebook.';
    return SafeArea(
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
            duration: const Duration(milliseconds: 200),
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
        return _ErrorSection(message: _error, onRetry: _lookUp);
      case _Look.ready:
        return _preview();
      case _Look.empty:
        if (_started == null) return const SizedBox.shrink();
        return Section(
          children: [
            GroupRow(
              title: 'Download started',
              subtitle: _started,
              value: 'View',
              chevron: true,
              onTap: widget.onGoToDownloads,
            ),
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

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Section(
          children: [
            Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                AspectRatio(
                  aspectRatio: 16 / 9,
                  child: MediaThumb(
                    url: info['thumbnail_url'] as String?,
                    audio: !_hasVideo,
                    radius: 0,
                    iconSize: 40,
                  ),
                ),
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 12, 16, 14),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        title,
                        maxLines: 3,
                        overflow: TextOverflow.ellipsis,
                        style: text.titleMedium,
                      ),
                      if (details.isNotEmpty) ...[
                        const SizedBox(height: 3),
                        Text(
                          details,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: text.bodyMedium?.copyWith(color: p.secondary),
                        ),
                      ],
                    ],
                  ),
                ),
              ],
            ),
          ],
        ),
        if (_hasVideo && _hasAudio)
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 28),
            child: CupertinoSlidingSegmentedControl<String>(
              groupValue: _mode,
              backgroundColor: p.fill,
              thumbColor: Theme.of(context).brightness == Brightness.dark
                  ? const Color(0xFF636366)
                  : Colors.white,
              padding: const EdgeInsets.all(2),
              onValueChanged: (value) {
                if (value != null) setState(() => _mode = value);
              },
              children: {
                for (final mode in ['Video', 'Audio'])
                  mode: Padding(
                    padding: const EdgeInsets.symmetric(vertical: 7),
                    child: Text(
                      mode == 'Video' ? 'Video' : 'Audio only',
                      style: text.bodyMedium?.copyWith(
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ),
              },
            ),
          ),
        if (video)
          Section(
            header: 'Quality',
            children: [
              for (var i = 0; i < _qualities.length; i++)
                GroupRow(
                  title: _qualities[i]['label'] as String,
                  value: _sizeText(_qualities[i]),
                  trailing: SizedBox(
                    width: 22,
                    child: i == _quality
                        ? Icon(Icons.check_rounded, color: p.label, size: 22)
                        : null,
                  ),
                  onTap: () => setState(() => _quality = i),
                ),
            ],
          )
        else
          const Section(
            header: 'Sound',
            footer: 'Plays on every phone and in music apps.',
            children: [GroupRow(title: 'M4A', value: 'Best quality')],
          ),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16),
          child: PrimaryButton(
            label: size.isEmpty ? 'Download' : 'Download · $size',
            onPressed: _download,
          ),
        ),
      ],
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
    return const Section(
      footer: 'Looking up the link…',
      children: [
        Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            AspectRatio(
              aspectRatio: 16 / 9,
              child: Pulse(height: double.infinity, radius: 0),
            ),
            Padding(
              padding: EdgeInsets.fromLTRB(16, 14, 16, 16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Pulse(height: 14),
                  SizedBox(height: 8),
                  Pulse(width: 160, height: 14),
                ],
              ),
            ),
          ],
        ),
      ],
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
