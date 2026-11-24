import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';

/// The app's look: soft neutral surfaces, one warm coral accent (the desktop
/// app's red, ui/theme.py) used as a gradient for the main actions.
class AppPalette extends ThemeExtension<AppPalette> {
  const AppPalette({
    required this.background,
    required this.card,
    required this.cardAlt,
    required this.border,
    required this.text,
    required this.muted,
    required this.success,
  });

  final Color background; // behind everything
  final Color card; // raised surfaces
  final Color cardAlt; // fields and wells inside cards
  final Color border; // hairlines
  final Color text;
  final Color muted; // secondary text and icons
  final Color success;

  static const accent = Color(0xFFF0465A);
  static const accentEnd = Color(0xFFFF8A4C);

  /// Coral to warm orange: the Download button, the logo, progress.
  static const gradient = LinearGradient(
    colors: [accent, accentEnd],
    begin: Alignment.topLeft,
    end: Alignment.bottomRight,
  );

  static const light = AppPalette(
    background: Color(0xFFF6F6F9),
    card: Color(0xFFFFFFFF),
    cardAlt: Color(0xFFF1F1F6),
    border: Color(0xFFE7E7EF),
    text: Color(0xFF14141B),
    muted: Color(0xFF6E6E7E),
    success: Color(0xFF1F9D61),
  );

  static const dark = AppPalette(
    background: Color(0xFF0C0C10),
    card: Color(0xFF17171D),
    cardAlt: Color(0xFF212129),
    border: Color(0xFF2A2A34),
    text: Color(0xFFF3F3F7),
    muted: Color(0xFF9B9BAC),
    success: Color(0xFF3CCB86),
  );

  @override
  AppPalette copyWith() => this;

  @override
  AppPalette lerp(AppPalette? other, double t) {
    if (other == null) return this;
    Color mix(Color a, Color b) => Color.lerp(a, b, t)!;
    return AppPalette(
      background: mix(background, other.background),
      card: mix(card, other.card),
      cardAlt: mix(cardAlt, other.cardAlt),
      border: mix(border, other.border),
      text: mix(text, other.text),
      muted: mix(muted, other.muted),
      success: mix(success, other.success),
    );
  }
}

extension PaletteOf on BuildContext {
  AppPalette get palette {
    final theme = Theme.of(this);
    return theme.extension<AppPalette>() ??
        (theme.brightness == Brightness.dark
            ? AppPalette.dark
            : AppPalette.light);
  }
}

const radius = 22.0; // cards
const fieldRadius = 16.0; // fields, buttons, chips

ThemeData buildTheme(Brightness brightness) {
  final dark = brightness == Brightness.dark;
  final p = dark ? AppPalette.dark : AppPalette.light;

  final scheme =
      ColorScheme.fromSeed(
        seedColor: AppPalette.accent,
        brightness: brightness,
      ).copyWith(
        primary: AppPalette.accent,
        onPrimary: Colors.white,
        surface: p.background,
        onSurface: p.text,
        onSurfaceVariant: p.muted,
        surfaceContainerLowest: p.card,
        surfaceContainerLow: p.card,
        surfaceContainer: p.card,
        surfaceContainerHigh: p.cardAlt,
        surfaceContainerHighest: p.cardAlt,
        outline: p.border,
        outlineVariant: p.border,
      );

  // The phone's own font (Roboto / San Francisco), in our weights
  final typography = Typography.material2021(platform: defaultTargetPlatform);
  // (englishLike first: its styles don't inherit, so they'd drop the font)
  final base = typography.englishLike.merge(
    dark ? typography.white : typography.black,
  );
  final text = base
      .copyWith(
        displaySmall: base.displaySmall?.copyWith(
          fontWeight: FontWeight.w800,
          letterSpacing: -1.2,
          height: 1.1,
        ),
        headlineMedium: base.headlineMedium?.copyWith(
          fontWeight: FontWeight.w800,
          letterSpacing: -0.8,
        ),
        titleLarge: base.titleLarge?.copyWith(
          fontWeight: FontWeight.w700,
          letterSpacing: -0.3,
        ),
        titleMedium: base.titleMedium?.copyWith(fontWeight: FontWeight.w700),
        titleSmall: base.titleSmall?.copyWith(fontWeight: FontWeight.w600),
        labelLarge: base.labelLarge?.copyWith(fontWeight: FontWeight.w700),
      )
      .apply(bodyColor: p.text, displayColor: p.text);

  final rounded = RoundedRectangleBorder(
    borderRadius: BorderRadius.circular(fieldRadius),
  );

  return ThemeData(
    useMaterial3: true,
    brightness: brightness,
    colorScheme: scheme,
    textTheme: text,
    scaffoldBackgroundColor: p.background,
    extensions: [p],
    splashFactory: InkSparkle.splashFactory,
    dividerTheme: DividerThemeData(color: p.border, space: 1, thickness: 1),
    cardTheme: CardThemeData(
      color: p.card,
      elevation: 0,
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(radius),
        side: BorderSide(color: p.border),
      ),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: false,
      border: InputBorder.none,
      hintStyle: text.bodyLarge?.copyWith(
        color: p.muted,
        fontWeight: FontWeight.w500,
      ),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        minimumSize: const Size(0, 48),
        shape: rounded,
        textStyle: text.labelLarge?.copyWith(fontSize: 15),
      ),
    ),
    textButtonTheme: TextButtonThemeData(
      style: TextButton.styleFrom(
        foregroundColor: AppPalette.accent,
        shape: rounded,
        textStyle: text.labelLarge,
      ),
    ),
    iconButtonTheme: IconButtonThemeData(
      style: IconButton.styleFrom(foregroundColor: p.muted),
    ),
    chipTheme: ChipThemeData(
      backgroundColor: p.cardAlt,
      selectedColor: AppPalette.accent.withValues(alpha: dark ? 0.22 : 0.12),
      side: BorderSide.none,
      shape: StadiumBorder(side: BorderSide(color: p.border)),
      labelStyle: text.labelLarge?.copyWith(color: p.text),
      secondaryLabelStyle: text.labelLarge?.copyWith(color: AppPalette.accent),
      checkmarkColor: AppPalette.accent,
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 8),
    ),
    switchTheme: SwitchThemeData(
      thumbColor: WidgetStateProperty.resolveWith(
        (states) =>
            states.contains(WidgetState.selected) ? Colors.white : p.muted,
      ),
      trackColor: WidgetStateProperty.resolveWith(
        (states) => states.contains(WidgetState.selected)
            ? AppPalette.accent
            : p.cardAlt,
      ),
      trackOutlineColor: WidgetStateProperty.resolveWith(
        (states) => states.contains(WidgetState.selected)
            ? AppPalette.accent
            : p.border,
      ),
    ),
    progressIndicatorTheme: ProgressIndicatorThemeData(
      color: AppPalette.accent,
      linearTrackColor: p.cardAlt,
      circularTrackColor: p.cardAlt,
    ),
    navigationBarTheme: NavigationBarThemeData(
      backgroundColor: p.card,
      surfaceTintColor: Colors.transparent,
      elevation: 0,
      height: 72,
      indicatorColor: AppPalette.accent.withValues(alpha: dark ? 0.2 : 0.12),
      indicatorShape: const StadiumBorder(),
      labelTextStyle: WidgetStateProperty.resolveWith(
        (states) => text.labelMedium?.copyWith(
          fontSize: 12,
          fontWeight: states.contains(WidgetState.selected)
              ? FontWeight.w700
              : FontWeight.w500,
          color: states.contains(WidgetState.selected)
              ? AppPalette.accent
              : p.muted,
        ),
      ),
      iconTheme: WidgetStateProperty.resolveWith(
        (states) => IconThemeData(
          color: states.contains(WidgetState.selected)
              ? AppPalette.accent
              : p.muted,
        ),
      ),
    ),
    badgeTheme: const BadgeThemeData(
      backgroundColor: AppPalette.accent,
      textColor: Colors.white,
    ),
    snackBarTheme: SnackBarThemeData(
      behavior: SnackBarBehavior.floating,
      backgroundColor: dark ? p.cardAlt : p.text,
      contentTextStyle: text.bodyMedium?.copyWith(
        color: dark ? p.text : p.background,
        fontWeight: FontWeight.w500,
      ),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(fieldRadius),
      ),
    ),
    dialogTheme: DialogThemeData(
      backgroundColor: p.card,
      surfaceTintColor: Colors.transparent,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(28)),
    ),
    listTileTheme: ListTileThemeData(
      iconColor: p.muted,
      contentPadding: const EdgeInsets.symmetric(horizontal: 16),
      titleTextStyle: text.titleMedium?.copyWith(
        color: p.text,
        fontSize: 16,
        fontWeight: FontWeight.w600,
      ),
      subtitleTextStyle: text.bodyMedium?.copyWith(
        color: p.muted,
        fontSize: 13,
        height: 1.35,
      ),
    ),
  );
}
