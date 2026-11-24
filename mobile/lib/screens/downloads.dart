import 'package:flutter/material.dart';

import '../models/download_item.dart';
import '../services/downloads.dart';

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
    final text = Theme.of(context).textTheme;

    return SafeArea(
      child: ListenableBuilder(
        listenable: downloads,
        builder: (context, _) {
          final current = downloads.current;
          final recent = downloads.recent;
          final anyFailed = recent.any((i) => i.state == DownloadState.failed);

          return ListView(
            padding: const EdgeInsets.fromLTRB(16, 20, 16, 32),
            children: [
              Text('Downloads', style: text.headlineMedium),
              const SizedBox(height: 16),
              _Heading(
                current.isEmpty
                    ? 'Current downloads'
                    : 'Current downloads (${current.length})',
              ),
              if (current.isEmpty)
                Card(
                  child: ListTile(
                    title: const Text('Nothing is downloading.'),
                    subtitle: const Text('Paste or share a link to start.'),
                    trailing: TextButton(
                      onPressed: onGoHome,
                      child: const Text('Go to Home'),
                    ),
                  ),
                ),
              for (final item in current)
                DownloadTile(item: item, downloads: downloads),
              const SizedBox(height: 20),
              _Heading(
                'Recent downloads',
                actions: [
                  if (anyFailed)
                    TextButton.icon(
                      onPressed: downloads.retryAllFailed,
                      icon: const Icon(Icons.refresh_rounded),
                      label: const Text('Retry failed'),
                    ),
                  if (recent.isNotEmpty)
                    TextButton(
                      onPressed: () => _confirmClear(context),
                      child: const Text('Clear list'),
                    ),
                ],
              ),
              if (recent.isEmpty)
                Padding(
                  padding: const EdgeInsets.all(8),
                  child: Text(
                    'Downloads you finish will show up here.',
                    style: text.bodyMedium?.copyWith(
                      color: Theme.of(context).colorScheme.onSurfaceVariant,
                    ),
                  ),
                ),
              for (final item in recent)
                DownloadTile(item: item, downloads: downloads),
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
          'Your downloaded files stay in your gallery. Only this list is cleared.',
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

class _Heading extends StatelessWidget {
  const _Heading(this.title, {this.actions = const []});

  final String title;
  final List<Widget> actions;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Row(
        children: [
          Expanded(
            child: Text(title, style: Theme.of(context).textTheme.titleLarge),
          ),
          ...actions,
        ],
      ),
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
    final scheme = Theme.of(context).colorScheme;
    final text = Theme.of(context).textTheme;
    final active = item.state.active;
    final failed = item.state == DownloadState.failed;

    return Card(
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: item.state == DownloadState.done ? () => _open(context) : null,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(12, 12, 4, 12),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              _Thumbnail(item: item),
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
                    if (item.meta.isNotEmpty)
                      Text(
                        item.meta,
                        style: text.bodySmall?.copyWith(
                          color: scheme.onSurfaceVariant,
                        ),
                      ),
                    if (active) ...[
                      const SizedBox(height: 8),
                      LinearProgressIndicator(
                        value: item.state == DownloadState.downloading ||
                                item.state == DownloadState.paused
                            ? item.progress
                            : null, // still getting ready / joining
                        color: item.state == DownloadState.paused
                            ? scheme.tertiary
                            : null,
                        borderRadius: BorderRadius.circular(4),
                      ),
                    ],
                    const SizedBox(height: 6),
                    Text(
                      item.statusText,
                      maxLines: 3,
                      overflow: TextOverflow.ellipsis,
                      style: text.bodySmall?.copyWith(
                        color: failed ? scheme.error : scheme.onSurfaceVariant,
                      ),
                    ),
                  ],
                ),
              ),
              ..._buttons(context),
            ],
          ),
        ),
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
          IconButton(
            tooltip: paused ? 'Resume' : 'Pause',
            onPressed: () => downloads.togglePause(item),
            icon: Icon(paused ? Icons.play_arrow_rounded : Icons.pause_rounded),
          ),
          IconButton(
            tooltip: 'Cancel',
            onPressed: () => downloads.cancel(item),
            icon: const Icon(Icons.close_rounded),
          ),
        ];
      case DownloadState.preparing || DownloadState.finishing:
        return [
          IconButton(
            tooltip: 'Cancel',
            onPressed: () => downloads.cancel(item),
            icon: const Icon(Icons.close_rounded),
          ),
        ];
      case DownloadState.done:
        return [
          IconButton(
            tooltip: 'Open',
            onPressed: () => _open(context),
            icon: const Icon(Icons.play_circle_outline_rounded),
          ),
          IconButton(
            tooltip: 'Remove from list',
            onPressed: () => downloads.remove(item),
            icon: const Icon(Icons.delete_outline_rounded),
          ),
        ];
      case DownloadState.failed || DownloadState.cancelled:
        return [
          IconButton(
            tooltip: 'Try again',
            onPressed: () => downloads.retry(item),
            icon: const Icon(Icons.refresh_rounded),
          ),
          IconButton(
            tooltip: 'Remove from list',
            onPressed: () => downloads.remove(item),
            icon: const Icon(Icons.delete_outline_rounded),
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

class _Thumbnail extends StatelessWidget {
  const _Thumbnail({required this.item});

  final DownloadItem item;

  @override
  Widget build(BuildContext context) {
    final placeholder = Icon(
      item.audioOnly ? Icons.music_note_outlined : Icons.movie_outlined,
      color: Theme.of(context).colorScheme.onSurfaceVariant,
    );

    return ClipRRect(
      borderRadius: BorderRadius.circular(8),
      child: Container(
        width: 96,
        height: 54,
        color: Theme.of(context).colorScheme.surfaceContainerHighest,
        child: item.thumbnailUrl == null
            ? placeholder
            : Image.network(
                item.thumbnailUrl!,
                fit: BoxFit.cover,
                errorBuilder: (_, _, _) => placeholder,
              ),
      ),
    );
  }
}
