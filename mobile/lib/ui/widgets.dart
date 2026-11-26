import 'dart:ui' show ImageFilter;

import 'package:flutter/material.dart';

import 'theme.dart';

/// A page's big title, as on iPhone ("Download", "Downloads", "Settings").
class LargeTitle extends StatelessWidget {
  const LargeTitle(this.title, {super.key, this.trailing});

  final String title;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 12, 12, 14),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          Expanded(
            child: Text(
              title,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: Theme.of(context).textTheme.headlineLarge,
            ),
          ),
          ?trailing,
        ],
      ),
    );
  }
}

/// An inset grouped list: optional small header, rows with hairlines
/// between them, optional footnote under it.
class Section extends StatelessWidget {
  const Section({
    super.key,
    this.header,
    this.headerAction,
    this.footer,
    required this.children,
  });

  final String? header;
  final Widget? headerAction;
  final String? footer;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final small = Theme.of(context).textTheme.bodySmall;
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 0, 16, 28),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (header != null || headerAction != null)
            Padding(
              padding: EdgeInsets.fromLTRB(
                16,
                0,
                4,
                headerAction == null ? 7 : 2,
              ),
              child: Row(
                children: [
                  Expanded(
                    child: Text(
                      header ?? '',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: small?.copyWith(color: p.secondary),
                    ),
                  ),
                  ?headerAction,
                ],
              ),
            ),
          if (children.isNotEmpty)
            Card(
              clipBehavior: Clip.antiAlias,
              child: Column(
                children: [
                  for (var i = 0; i < children.length; i++) ...[
                    if (i > 0) const Divider(indent: 16),
                    children[i],
                  ],
                ],
              ),
            ),
          if (footer != null)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 7, 16, 0),
              child: Text(footer!, style: small?.copyWith(color: p.secondary)),
            ),
        ],
      ),
    );
  }
}

/// One row in a [Section]: a title, maybe a line under it, and a value,
/// switch, checkmark or chevron on the right.
class GroupRow extends StatelessWidget {
  const GroupRow({
    super.key,
    required this.title,
    this.subtitle,
    this.value,
    this.trailing,
    this.onTap,
    this.chevron = false,
    this.destructive = false,
  });

  final String title;
  final String? subtitle;
  final String? value;
  final Widget? trailing;
  final VoidCallback? onTap;
  final bool chevron;
  final bool destructive;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final text = Theme.of(context).textTheme;
    return InkWell(
      onTap: onTap,
      child: ConstrainedBox(
        constraints: const BoxConstraints(minHeight: 48),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 11, 14, 11),
          child: Row(
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      title,
                      style: text.bodyLarge?.copyWith(
                        color: destructive ? p.red : p.label,
                      ),
                    ),
                    if (subtitle != null)
                      Padding(
                        padding: const EdgeInsets.only(top: 2),
                        child: Text(
                          subtitle!,
                          style: text.bodySmall?.copyWith(color: p.secondary),
                        ),
                      ),
                  ],
                ),
              ),
              if (value != null)
                ConstrainedBox(
                  // a value sits at the right, next to its checkmark
                  constraints: const BoxConstraints(maxWidth: 200),
                  child: Padding(
                    padding: const EdgeInsets.only(left: 12),
                    child: Text(
                      value!,
                      textAlign: TextAlign.right,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: text.bodyLarge?.copyWith(color: p.secondary),
                    ),
                  ),
                ),
              if (trailing != null)
                Padding(
                  padding: const EdgeInsets.only(left: 12),
                  child: trailing,
                ),
              if (chevron)
                Padding(
                  padding: const EdgeInsets.only(left: 6),
                  child: Icon(
                    Icons.chevron_right_rounded,
                    color: p.tertiary,
                    size: 22,
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

/// The one main action on a screen: solid brand red, full width.
class PrimaryButton extends StatelessWidget {
  const PrimaryButton({
    super.key,
    required this.label,
    required this.onPressed,
    this.icon,
  });

  final String label;
  final IconData? icon;
  final VoidCallback? onPressed;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return SizedBox(
      height: 54,
      child: FilledButton.icon(
        onPressed: onPressed,
        icon: icon == null ? null : Icon(icon, size: 20),
        label: Text(label),
        style: FilledButton.styleFrom(
          backgroundColor: p.accent,
          foregroundColor: Colors.white,
          disabledBackgroundColor: p.fill,
          elevation: 0,
          textStyle: Theme.of(context).textTheme.labelLarge,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(14),
          ),
        ),
      ),
    );
  }
}

/// A video's picture, large and rounded, lifted off the page.
class Artwork extends StatelessWidget {
  const Artwork({
    super.key,
    required this.url,
    required this.audio,
    this.radius = 16,
    this.iconSize = 40,
  });

  final String? url;
  final bool audio;
  final double radius;
  final double iconSize;

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(radius),
        boxShadow: const [
          BoxShadow(
            color: Color(0x59000000),
            blurRadius: 30,
            offset: Offset(0, 14),
          ),
        ],
      ),
      child: MediaThumb(
        url: url,
        audio: audio,
        radius: radius,
        iconSize: iconSize,
      ),
    );
  }
}

/// The video's own picture, blurred, filling the top of the screen and
/// fading into the background (as Apple Music / TV do).
class Backdrop extends StatelessWidget {
  const Backdrop({super.key, required this.url, this.height = 460});

  final String? url;
  final double height;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return SizedBox(
      height: height,
      width: double.infinity,
      child: Stack(
        fit: StackFit.expand,
        children: [
          if (url != null)
            ImageFiltered(
              imageFilter: ImageFilter.blur(sigmaX: 50, sigmaY: 50),
              child: Image.network(
                url!,
                fit: BoxFit.cover,
                errorBuilder: (_, _, _) => const SizedBox.shrink(),
              ),
            ),
          DecoratedBox(
            decoration: BoxDecoration(
              gradient: LinearGradient(
                begin: Alignment.topCenter,
                end: Alignment.bottomCenter,
                colors: [
                  p.background.withValues(alpha: 0.35),
                  p.background.withValues(alpha: 0.75),
                  p.background,
                ],
                stops: const [0, 0.6, 1],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// Two or more choices in a pill track, one picked (Video / Audio).
class PillToggle<T> extends StatelessWidget {
  const PillToggle({
    super.key,
    required this.value,
    required this.options,
    required this.onChanged,
  });

  final T value;
  final Map<T, String> options;
  final ValueChanged<T> onChanged;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final text = Theme.of(context).textTheme;
    return Container(
      height: 44,
      padding: const EdgeInsets.all(4),
      decoration: BoxDecoration(
        color: p.fill,
        borderRadius: BorderRadius.circular(22),
      ),
      child: Row(
        children: [
          for (final entry in options.entries)
            Expanded(
              child: Semantics(
                button: true,
                selected: entry.key == value,
                child: GestureDetector(
                  behavior: HitTestBehavior.opaque,
                  onTap: () => onChanged(entry.key),
                  child: AnimatedContainer(
                    duration: const Duration(milliseconds: 180),
                    alignment: Alignment.center,
                    decoration: BoxDecoration(
                      color: entry.key == value ? p.label : Colors.transparent,
                      borderRadius: BorderRadius.circular(18),
                    ),
                    child: Text(
                      entry.value,
                      style: text.bodyMedium?.copyWith(
                        fontWeight: FontWeight.w600,
                        color: entry.key == value ? p.onLabel : p.secondary,
                      ),
                    ),
                  ),
                ),
              ),
            ),
        ],
      ),
    );
  }
}

/// One quality to pick: "1080p" over "182 MB".
class QualityPill extends StatelessWidget {
  const QualityPill({
    super.key,
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
    final text = Theme.of(context).textTheme;
    return Semantics(
      button: true,
      selected: selected,
      child: GestureDetector(
        onTap: onTap,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 180),
          width: 92,
          padding: const EdgeInsets.symmetric(vertical: 10),
          decoration: BoxDecoration(
            color: selected ? p.accent : p.fill,
            borderRadius: BorderRadius.circular(14),
          ),
          child: Column(
            children: [
              Text(
                label,
                style: text.titleMedium?.copyWith(
                  color: selected ? Colors.white : p.label,
                ),
              ),
              if (size.isNotEmpty)
                Text(
                  size,
                  style: text.bodySmall?.copyWith(
                    color: selected ? Colors.white70 : p.secondary,
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

/// A picture from the web, with a plain grey placeholder.
class MediaThumb extends StatelessWidget {
  const MediaThumb({
    super.key,
    required this.url,
    required this.audio,
    this.radius = smallRadius,
    this.iconSize = 22,
  });

  final String? url;
  final bool audio;
  final double radius;
  final double iconSize;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final placeholder = ColoredBox(
      color: p.fill,
      child: Center(
        child: Icon(
          audio ? Icons.music_note_rounded : Icons.play_arrow_rounded,
          color: p.tertiary,
          size: iconSize,
        ),
      ),
    );
    return ClipRRect(
      borderRadius: BorderRadius.circular(radius),
      child: url == null
          ? placeholder
          : Image.network(
              url!,
              fit: BoxFit.cover,
              errorBuilder: (_, _, _) => placeholder,
              frameBuilder: (_, child, frame, sync) =>
                  sync || frame != null ? child : placeholder,
            ),
    );
  }
}

/// A grey block that fades in and out while something loads.
class Pulse extends StatefulWidget {
  const Pulse({super.key, this.width, required this.height, this.radius = 4});

  final double? width;
  final double height;
  final double radius;

  @override
  State<Pulse> createState() => _PulseState();
}

class _PulseState extends State<Pulse> with SingleTickerProviderStateMixin {
  late final _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 900),
  )..repeat(reverse: true);

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return FadeTransition(
      opacity: Tween(
        begin: 0.5,
        end: 1.0,
      ).animate(CurvedAnimation(parent: _controller, curve: Curves.easeInOut)),
      child: Container(
        width: widget.width,
        height: widget.height,
        decoration: BoxDecoration(
          color: context.palette.fill,
          borderRadius: BorderRadius.circular(widget.radius),
        ),
      ),
    );
  }
}

/// Nothing here yet: a quiet icon and two lines, centred.
class EmptyState extends StatelessWidget {
  const EmptyState({
    super.key,
    required this.icon,
    required this.title,
    required this.message,
    this.action,
  });

  final IconData icon;
  final String title;
  final String message;
  final Widget? action;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final text = Theme.of(context).textTheme;
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 40, vertical: 80),
      child: Column(
        children: [
          Icon(icon, size: 44, color: p.tertiary),
          const SizedBox(height: 14),
          Text(title, style: text.titleLarge, textAlign: TextAlign.center),
          const SizedBox(height: 6),
          Text(
            message,
            textAlign: TextAlign.center,
            style: text.bodyMedium?.copyWith(color: p.secondary),
          ),
          if (action != null) ...[const SizedBox(height: 12), action!],
        ],
      ),
    );
  }
}
