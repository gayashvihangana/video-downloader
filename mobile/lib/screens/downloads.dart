import 'package:flutter/material.dart';

import '../models/download_item.dart';
import '../services/downloads.dart';
import '../ui/theme.dart';
import '../ui/widgets.dart';
import '../util/format.dart';

/// Current downloads (progress, pause, cancel) and recent ones (open, retry).
class DownloadsScreen extends StatelessWidget {
  const DownloadsScreen({
    super.key,
    required this.downloads,
    required this.onGoHome,
  });

  final Downloads downloads;
  final VoidCallback onGoHome;

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      bottom: false,
      child: ListenableBuilder(
        listenable: downloads,
        builder: (context, _) {
          final current = downloads.current;
          final recent = downloads.recent;
          final saved = recent.where((i) => i.state == DownloadState.done);
          final anyFailed = recent.any((i) => i.state == DownloadState.failed);

          final summary = [
            if (current.isNotEmpty) '${current.length} in progress',
            if (saved.isNotEmpty) '${saved.length} saved',
          ];

          return ListView(
            padding: const EdgeInsets.fromLTRB(20, 20, 20, 32),
            children: [
              PageHeader(
                'Downloads',
                subtitle: summary.isEmpty
                    ? 'Everything you save shows up here.'
                    : summary.join('  ·  '),
              ),
              if (current.isEmpty && recent.isEmpty)
                EmptyState(
                  icon: Icons.download_rounded,
                  title: 'No downloads yet',
                  message:
                      'Paste or share a link and your downloads will show '
                      'up here.',
                  action: FilledButton.icon(
                    onPressed: onGoHome,
                    icon: const Icon(Icons.add_rounded),
                    label: const Text('New download'),
                  ),
                )
              else ...[
                if (current.isNotEmpty) ...[
                  SectionTitle('In progress', count: current.length),
                  for (final item in current)
                    DownloadTile(item: item, downloads: downloads),
                  const SizedBox(height: 18),
                ],
                if (recent.isNotEmpty) ...[
                  SectionTitle(
                    'Recent',
                    actions: [
                      if (anyFailed)
                        _SmallButton(
                          label: 'Retry failed',
                          icon: Icons.refresh_rounded,
                          onPressed: downloads.retryAllFailed,
                        ),
                      _SmallButton(
                        label: 'Clear',
                        onPressed: () => _confirmClear(context),
                      ),
                    ],
                  ),
                  for (final item in recent)
                    DownloadTile(item: item, downloads: downloads),
                ],
              ],
            ],
          );
        },
      ),
    );
  }

  Future<void> _confirmClear(BuildContext context) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Clear the list?'),
        content: const Text(
          'Your downloaded files stay in your gallery. Only this list is '
          'cleared.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Keep'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Clear list'),
          ),
        ],
      ),
    );
    if (ok == true) downloads.clearRecent();
  }
}

class _SmallButton extends StatelessWidget {
  const _SmallButton({required this.label, required this.onPressed, this.icon});

  final String label;
  final IconData? icon;
  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) {
    final style = TextButton.styleFrom(
      visualDensity: VisualDensity.compact,
      padding: const EdgeInsets.symmetric(horizontal: 10),
      textStyle: Theme.of(context).textTheme.labelLarge?.copyWith(fontSize: 13),
    );
    return icon == null
        ? TextButton(onPressed: onPressed, style: style, child: Text(label))
        : TextButton.icon(
            onPressed: onPressed,
            style: style,
            icon: Icon(icon, size: 16),
            label: Text(label),
          );
  }
}

/// One download: picture, title, progress and its buttons.
class DownloadTile extends StatelessWidget {
  const DownloadTile({super.key, required this.item, required this.downloads});

  final DownloadItem item;
  final Downloads downloads;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final text = Theme.of(context).textTheme;
    final active = item.state.active;

    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Panel(
        onTap: item.state == DownloadState.done ? () => _open(context) : null,
        padding: const EdgeInsets.all(10),
        child: Column(
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SizedBox(
                  width: 104,
                  height: 64,
                  child: Stack(
                    fit: StackFit.expand,
                    children: [
                      MediaThumb(
                        url: item.thumbnailUrl,
                        audio: item.audioOnly,
                        radius: 12,
                      ),
                      if (item.state == DownloadState.done)
                        Center(
                          child: Container(
                            width: 30,
                            height: 30,
                            decoration: BoxDecoration(
                              color: Colors.black.withValues(alpha: 0.55),
                              shape: BoxShape.circle,
                            ),
                            child: Icon(
                              item.audioOnly
                                  ? Icons.music_note_rounded
                                  : Icons.play_arrow_rounded,
                              color: Colors.white,
                              size: 20,
                            ),
                          ),
                        ),
                    ],
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        item.name,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: text.titleSmall?.copyWith(height: 1.25),
                      ),
                      const SizedBox(height: 6),
                      Wrap(spacing: 6, runSpacing: 4, children: _tags(context)),
                      ?_doneLine(context),
                    ],
                  ),
                ),
                // In progress / failed: buttons sit lower, so the title
                // gets the full width
                if (!active && item.state != DownloadState.failed)
                  ..._buttons(context),
              ],
            ),
            if (active) ...[
              const SizedBox(height: 12),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 4),
                child: Column(
                  children: [
                    GradientProgress(
                      value:
                          item.state == DownloadState.downloading ||
                              item.state == DownloadState.paused
                          ? item.progress
                          : null, // still getting ready / joining
                      paused: item.state == DownloadState.paused,
                    ),
                    const SizedBox(height: 4),
                    Row(
                      children: [
                        Expanded(
                          child: Text(
                            item.statusText,
                            maxLines: 2,
                            overflow: TextOverflow.ellipsis,
                            style: TextStyle(
                              color: p.muted,
                              fontSize: 12.5,
                              fontWeight: FontWeight.w500,
                            ),
                          ),
                        ),
                        ..._buttons(context),
                      ],
                    ),
                  ],
                ),
              ),
            ],
            if (item.state == DownloadState.failed) ...[
              const SizedBox(height: 10),
              Row(
                children: [
                  Expanded(
                    child: Container(
                      width: double.infinity,
                      padding: const EdgeInsets.symmetric(
                        horizontal: 12,
                        vertical: 9,
                      ),
                      decoration: BoxDecoration(
                        color: Theme.of(context).colorScheme.error
                            .withValues(alpha: 0.08),
                        borderRadius: BorderRadius.circular(12),
                      ),
                      child: Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Icon(
                            Icons.error_outline_rounded,
                            size: 17,
                            color: Theme.of(context).colorScheme.error,
                          ),
                          const SizedBox(width: 8),
                          Expanded(
                            child: Text(
                              item.error.isEmpty
                                  ? 'Download failed.'
                                  : item.error,
                              style: TextStyle(
                                color: Theme.of(context).colorScheme.error,
                                fontSize: 13,
                                fontWeight: FontWeight.w500,
                                height: 1.3,
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                  ..._buttons(context),
                ],
              ),
            ],
          ],
        ),
      ),
    );
  }

  List<Widget> _tags(BuildContext context) => [
    if (item.site.isNotEmpty)
      Tag(item.site, color: siteColor(item.site, context)),
    Tag(
      item.audioOnly ? 'M4A' : (item.quality.isEmpty ? 'Video' : item.quality),
    ),
  ];

  /// "✓ Saved · 48 MB · 1:49 AM" / "Cancelled · Yesterday"
  Widget? _doneLine(BuildContext context) {
    final p = context.palette;
    final done = item.state == DownloadState.done;
    if (!done && item.state != DownloadState.cancelled) return null;
    final color = done ? p.success : p.muted;
    final parts = [
      done ? 'Saved' : 'Cancelled',
      if (done && item.size > 0) humanSize(item.size),
      when(item.finishedAt),
    ].where((part) => part.isNotEmpty);
    return Padding(
      padding: const EdgeInsets.only(top: 6),
      child: Row(
        children: [
          Icon(
            done ? Icons.check_circle_rounded : Icons.block_rounded,
            size: 15,
            color: color,
          ),
          const SizedBox(width: 5),
          Expanded(
            child: Text(
              parts.join(' · '),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(
                color: color,
                fontSize: 12.5,
                fontWeight: FontWeight.w600,
              ),
            ),
          ),
        ],
      ),
    );
  }

  List<Widget> _buttons(BuildContext context) {
    switch (item.state) {
      case DownloadState.downloading ||
          DownloadState.waiting ||
          DownloadState.paused:
        final paused = item.state == DownloadState.paused;
        return [
          _RoundButton(
            tooltip: paused ? 'Resume' : 'Pause',
            icon: paused ? Icons.play_arrow_rounded : Icons.pause_rounded,
            onPressed: () => downloads.togglePause(item),
            filled: true,
          ),
          _RoundButton(
            tooltip: 'Cancel',
            icon: Icons.close_rounded,
            onPressed: () => downloads.cancel(item),
          ),
        ];
      case DownloadState.preparing || DownloadState.finishing:
        return [
          _RoundButton(
            tooltip: 'Cancel',
            icon: Icons.close_rounded,
            onPressed: () => downloads.cancel(item),
          ),
        ];
      case DownloadState.done:
        return [
          _RoundButton(
            tooltip: 'Remove from list',
            icon: Icons.delete_outline_rounded,
            onPressed: () => downloads.remove(item),
          ),
        ];
      case DownloadState.failed || DownloadState.cancelled:
        return [
          _RoundButton(
            tooltip: 'Try again',
            icon: Icons.refresh_rounded,
            onPressed: () => downloads.retry(item),
            filled: true,
          ),
          _RoundButton(
            tooltip: 'Remove from list',
            icon: Icons.delete_outline_rounded,
            onPressed: () => downloads.remove(item),
          ),
        ];
    }
  }

  Future<void> _open(BuildContext context) async {
    final messenger = ScaffoldMessenger.of(context);
    if (!await downloads.open(item)) {
      messenger.showSnackBar(
        const SnackBar(
          content: Text(
            "Couldn't open it here. Find it in your gallery: "
            'Video Downloader album.',
          ),
        ),
      );
    }
  }
}

class _RoundButton extends StatelessWidget {
  const _RoundButton({
    required this.tooltip,
    required this.icon,
    required this.onPressed,
    this.filled = false,
  });

  final String tooltip;
  final IconData icon;
  final VoidCallback onPressed;
  final bool filled;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Padding(
      padding: const EdgeInsets.only(left: 4),
      child: IconButton(
        tooltip: tooltip,
        onPressed: onPressed,
        icon: Icon(icon, size: 20),
        style: IconButton.styleFrom(
          fixedSize: const Size(38, 38),
          minimumSize: const Size(38, 38),
          backgroundColor: filled
              ? AppPalette.accent.withValues(alpha: 0.12)
              : p.cardAlt,
          foregroundColor: filled ? AppPalette.accent : p.muted,
        ),
      ),
    );
  }
}
