import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';

/// The app's colours: Apple's system greys (they read as native on iPhone
/// and calm on Android), ink for actions, and colour only where it means
/// something (red: failed / destructive, green: done).
class AppPalette extends ThemeExtension<AppPalette> {
  const AppPalette({
    required this.background,
    required this.group,
    required this.fill,
    required this.separator,
    required this.label,
    required this.onLabel,
    required this.secondary,
    required this.tertiary,
    required this.red,
    required this.green,
  });

  final Color background; // behind the grouped lists
  final Color group; // the lists / cards themselves
  final Color fill; // fields, thumbnails, tracks
  final Color separator; // hairlines
  final Color label; // text, and the main button ("ink")
  final Color onLabel; // text on the main button
  final Color secondary; // supporting text
  final Color tertiary; // placeholders, chevrons
  final Color red;
  final Color green;

  static const light = AppPalette(
    background: Color(0xFFF2F2F7),
    group: Color(0xFFFFFFFF),
    fill: Color(0xFFE9E9EE),
    separator: Color(0xFFDCDCE0),
    label: Color(0xFF111113),
    onLabel: Color(0xFFFFFFFF),
    secondary: Color(0xFF86868B),
    tertiary: Color(0xFFC1C1C6),
    red: Color(0xFFE5342B),
    green: Color(0xFF28A745),
  );

  static const dark = AppPalette(
    background: Color(0xFF000000),
    group: Color(0xFF1C1C1E),
    fill: Color(0xFF2C2C2E),
    separator: Color(0xFF38383A),
    label: Color(0xFFF5F5F7),
    onLabel: Color(0xFF000000),
    secondary: Color(0xFF8E8E93),
    tertiary: Color(0xFF545458),
    red: Color(0xFFFF453A),
    green: Color(0xFF32D74B),
  );

  @override
  AppPalette copyWith() => this;

  @override
  AppPalette lerp(AppPalette? other, double t) {
    if (other == null) return this;
    Color mix(Color a, Color b) => Color.lerp(a, b, t)!;
    return AppPalette(
      background: mix(background, other.background),
      group: mix(group, other.group),
      fill: mix(fill, other.fill),
      separator: mix(separator, other.separator),
      label: mix(label, other.label),
      onLabel: mix(onLabel, other.onLabel),
      secondary: mix(secondary, other.secondary),
      tertiary: mix(tertiary, other.tertiary),
      red: mix(red, other.red),
      green: mix(green, other.green),
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

/// One rounding scale for everything.
const groupRadius = 12.0; // lists, cards, the main button
const smallRadius = 8.0; // thumbnails, fields

bool get _apple =>
    defaultTargetPlatform == TargetPlatform.iOS ||
    defaultTargetPlatform == TargetPlatform.macOS;

ThemeData buildTheme(Brightness brightness) {
  final dark = brightness == Brightness.dark;
  final p = dark ? AppPalette.dark : AppPalette.light;

  final scheme =
      ColorScheme.fromSeed(
        seedColor: p.label,
        brightness: brightness,
        dynamicSchemeVariant: DynamicSchemeVariant.monochrome,
      ).copyWith(
        primary: p.label,
        onPrimary: p.onLabel,
        secondary: p.label,
        error: p.red,
        surface: p.background,
        onSurface: p.label,
        onSurfaceVariant: p.secondary,
        surfaceContainerLowest: p.group,
        surfaceContainerLow: p.group,
        surfaceContainer: p.group,
        surfaceContainerHigh: p.fill,
        surfaceContainerHighest: p.fill,
        outline: p.separator,
        outlineVariant: p.separator,
      );

  // The phone's own font (San Francisco / Roboto) at iOS text sizes.
  // (englishLike first: its styles don't inherit, so they'd drop the font)
  final typography = Typography.material2021(platform: defaultTargetPlatform);
  final base = typography.englishLike.merge(
    dark ? typography.white : typography.black,
  );
  TextStyle? size(TextStyle? style, double size, FontWeight weight) =>
      style?.copyWith(
        fontSize: size,
        fontWeight: weight,
        letterSpacing: 0,
        height: 1.25,
      );
  final text = base
      .copyWith(
        headlineLarge: size(base.headlineLarge, 34, FontWeight.w700),
        titleLarge: size(base.titleLarge, 20, FontWeight.w600),
        titleMedium: size(base.titleMedium, 17, FontWeight.w600),
        titleSmall: size(base.titleSmall, 15, FontWeight.w600),
        bodyLarge: size(base.bodyLarge, 17, FontWeight.w400),
        bodyMedium: size(base.bodyMedium, 15, FontWeight.w400),
        bodySmall: size(base.bodySmall, 13, FontWeight.w400),
        labelLarge: size(base.labelLarge, 17, FontWeight.w600),
        labelMedium: size(base.labelMedium, 13, FontWeight.w500),
        labelSmall: size(base.labelSmall, 11, FontWeight.w500),
      )
      .apply(bodyColor: p.label, displayColor: p.label);

  return ThemeData(
    useMaterial3: true,
    brightness: brightness,
    colorScheme: scheme,
    textTheme: text,
    scaffoldBackgroundColor: p.background,
    extensions: [p],
    // iPhone apps don't ripple: a quiet highlight instead
    splashFactory: _apple ? NoSplash.splashFactory : InkRipple.splashFactory,
    highlightColor: p.fill.withValues(alpha: 0.7),
    dividerTheme: DividerThemeData(
      color: p.separator,
      space: 0.5,
      thickness: 0.5,
    ),
    cardTheme: CardThemeData(
      color: p.group,
      elevation: 0,
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(groupRadius),
      ),
    ),
    inputDecorationTheme: InputDecorationTheme(
      border: InputBorder.none,
      hintStyle: text.bodyLarge?.copyWith(color: p.tertiary),
    ),
    textButtonTheme: TextButtonThemeData(
      style: TextButton.styleFrom(
        foregroundColor: p.label,
        textStyle: text.bodyLarge?.copyWith(fontWeight: FontWeight.w600),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(smallRadius),
        ),
      ),
    ),
    iconButtonTheme: IconButtonThemeData(
      style: IconButton.styleFrom(foregroundColor: p.label),
    ),
    progressIndicatorTheme: ProgressIndicatorThemeData(
      color: p.label,
      linearTrackColor: p.fill,
      circularTrackColor: Colors.transparent,
    ),
    switchTheme: SwitchThemeData(
      trackColor: WidgetStateProperty.resolveWith(
        (states) => states.contains(WidgetState.selected) ? p.green : p.fill,
      ),
      thumbColor: const WidgetStatePropertyAll(Colors.white),
      trackOutlineColor: const WidgetStatePropertyAll(Colors.transparent),
    ),
    navigationBarTheme: NavigationBarThemeData(
      backgroundColor: dark ? const Color(0xFF0E0E10) : const Color(0xFFF9F9FB),
      surfaceTintColor: Colors.transparent,
      elevation: 0,
      height: 58,
      indicatorColor: Colors.transparent,
      labelTextStyle: WidgetStateProperty.resolveWith(
        (states) => text.labelSmall?.copyWith(
          fontSize: 10.5,
          color: states.contains(WidgetState.selected) ? p.label : p.secondary,
        ),
      ),
      iconTheme: WidgetStateProperty.resolveWith(
        (states) => IconThemeData(
          size: 25,
          color: states.contains(WidgetState.selected) ? p.label : p.secondary,
        ),
      ),
    ),
    badgeTheme: BadgeThemeData(backgroundColor: p.red, textColor: Colors.white),
    snackBarTheme: SnackBarThemeData(
      behavior: SnackBarBehavior.floating,
      backgroundColor: dark ? p.fill : p.label,
      contentTextStyle: text.bodyMedium?.copyWith(
        color: dark ? p.label : Colors.white,
      ),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(groupRadius),
      ),
    ),
    dialogTheme: DialogThemeData(
      backgroundColor: p.group,
      surfaceTintColor: Colors.transparent,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
    ),
  );
}
