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
          final anyFailed = recent.any((i) => i.state == DownloadState.failed);

          return ListView(
            padding: const EdgeInsets.only(bottom: 32),
            children: [
              const LargeTitle('Downloads'),
              if (current.isEmpty && recent.isEmpty)
                EmptyState(
                  icon: Icons.download_for_offline_outlined,
                  title: 'No Downloads',
                  message: 'Videos and music you download appear here.',
                  action: TextButton(
                    onPressed: onGoHome,
                    child: const Text('Download Something'),
                  ),
                ),
              if (current.isNotEmpty)
                Section(
                  header: 'Downloading',
                  children: [
                    for (final item in current)
                      DownloadTile(item: item, downloads: downloads),
                  ],
                ),
              if (recent.isNotEmpty)
                Section(
                  header: 'Recent',
                  headerAction: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      if (anyFailed)
                        _HeaderButton('Retry Failed', downloads.retryAllFailed),
                      _HeaderButton('Clear', () => _confirmClear(context)),
                    ],
                  ),
                  footer: 'Swipe left on a download to remove it.',
                  children: [
                    for (final item in recent)
                      Dismissible(
                        key: ValueKey(item.id),
                        direction: DismissDirection.endToStart,
                        onDismissed: (_) => downloads.remove(item),
                        background: ColoredBox(
                          color: context.palette.red,
                          child: const Align(
                            alignment: Alignment.centerRight,
                            child: Padding(
                              padding: EdgeInsets.only(right: 20),
                              child: Text(
                                'Remove',
                                style: TextStyle(
                                  color: Colors.white,
                                  fontWeight: FontWeight.w600,
                                ),
                              ),
                            ),
                          ),
                        ),
                        child: DownloadTile(item: item, downloads: downloads),
                      ),
                  ],
                ),
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
        title: const Text('Clear Recent Downloads?'),
        content: const Text(
          'The videos stay in your gallery. Only this list is cleared.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            style: TextButton.styleFrom(foregroundColor: context.palette.red),
            child: const Text('Clear'),
          ),
        ],
      ),
    );
    if (ok == true) downloads.clearRecent();
  }
}

class _HeaderButton extends StatelessWidget {
  const _HeaderButton(this.label, this.onPressed);

  final String label;
  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) {
    return TextButton(
      onPressed: onPressed,
      style: TextButton.styleFrom(
        visualDensity: VisualDensity.compact,
        padding: const EdgeInsets.symmetric(horizontal: 8),
        minimumSize: const Size(0, 28),
        textStyle: Theme.of(context).textTheme.bodySmall
            ?.copyWith(fontWeight: FontWeight.w600),
      ),
      child: Text(label),
    );
  }
}

/// One download: picture, title, what it's doing, and its buttons.
class DownloadTile extends StatelessWidget {
  const DownloadTile({super.key, required this.item, required this.downloads});

  final DownloadItem item;
  final Downloads downloads;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final text = Theme.of(context).textTheme;
    final active = item.state.active;
    final failed = item.state == DownloadState.failed;

    return InkWell(
      onTap: item.state == DownloadState.done ? () => _open(context) : null,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(12, 12, 6, 12),
        child: Row(
          children: [
            SizedBox(
              width: 104,
              height: 58,
              child: MediaThumb(
                url: item.thumbnailUrl,
                audio: item.audioOnly,
                radius: 10,
                iconSize: 20,
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
                    style: text.titleSmall,
                  ),
                  const SizedBox(height: 3),
                  Text(
                    _line(),
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: text.bodySmall?.copyWith(
                      color: failed ? p.red : p.secondary,
                    ),
                  ),
                  if (active) ...[
                    const SizedBox(height: 7),
                    ClipRRect(
                      borderRadius: BorderRadius.circular(2),
                      child: LinearProgressIndicator(
                        minHeight: 3,
                        value:
                            item.state == DownloadState.downloading ||
                                item.state == DownloadState.paused
                            ? item.progress
                            : null, // still getting ready / joining
                        color: item.state == DownloadState.paused
                            ? p.secondary
                            : p.accent,
                      ),
                    ),
                  ],
                ],
              ),
            ),
            ..._buttons(),
          ],
        ),
      ),
    );
  }

  /// The grey line under the title.
  String _line() {
    final about = [
      item.site,
      if (item.audioOnly) 'Audio' else item.quality,
    ].where((part) => part.isNotEmpty);
    return switch (item.state) {
      DownloadState.done => [
        ...about,
        if (item.size > 0) humanSize(item.size),
        when(item.finishedAt),
      ].where((part) => part.isNotEmpty).join(' · '),
      DownloadState.failed =>
        item.error.isEmpty ? 'Download failed.' : item.error,
      DownloadState.cancelled => ['Cancelled', ...about].join(' · '),
      // keep "182 MB" and "3.4 MB/s" whole when the line wraps
      _ =>
        item.statusText
            .split(' · ')
            .map((part) => part.replaceAll(' ', ' '))
            .join(' · '),
    };
  }

  List<Widget> _buttons() {
    IconButton button(String tip, IconData icon, VoidCallback onPressed) =>
        IconButton(
          tooltip: tip,
          onPressed: onPressed,
          icon: Icon(icon, size: 22),
          visualDensity: VisualDensity.compact,
        );

    switch (item.state) {
      case DownloadState.downloading ||
          DownloadState.waiting ||
          DownloadState.paused:
        final paused = item.state == DownloadState.paused;
        return [
          button(
            paused ? 'Resume' : 'Pause',
            paused ? Icons.play_arrow_rounded : Icons.pause_rounded,
            () => downloads.togglePause(item),
          ),
          button('Cancel', Icons.close_rounded, () => downloads.cancel(item)),
        ];
      case DownloadState.preparing || DownloadState.finishing:
        return [
          button('Cancel', Icons.close_rounded, () => downloads.cancel(item)),
        ];
      case DownloadState.failed || DownloadState.cancelled:
        return [
          button(
            'Try again',
            Icons.refresh_rounded,
            () => downloads.retry(item),
          ),
        ];
      case DownloadState.done:
        return const [];
    }
  }

  Future<void> _open(BuildContext context) async {
    final messenger = ScaffoldMessenger.of(context);
    if (!await downloads.open(item)) {
      messenger.showSnackBar(
        const SnackBar(
          content: Text(
            "Couldn't open it here. It's in your gallery, in the Video "
            'Downloader album.',
          ),
        ),
      );
    }
  }
}
