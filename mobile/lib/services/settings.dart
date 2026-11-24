import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// The app's settings, remembered between launches.
class Settings extends ChangeNotifier {
  Settings(this._prefs);

  final SharedPreferences _prefs;

  ThemeMode get themeMode =>
      ThemeMode.values.asNameMap()[_prefs.getString('theme')] ??
      ThemeMode.system;

  set themeMode(ThemeMode mode) {
    _prefs.setString('theme', mode.name);
    notifyListeners();
  }

  /// Download only on Wi-Fi (saves mobile data)
  bool get wifiOnly => _prefs.getBool('wifiOnly') ?? false;

  set wifiOnly(bool value) {
    _prefs.setBool('wifiOnly', value);
    notifyListeners();
  }

  /// "Video" or "Audio": what was picked last time
  String get lastMode => _prefs.getString('mode') ?? 'Video';

  set lastMode(String value) => _prefs.setString('mode', value);

  /// The quality picked last time ("1080p"), kept for the next links
  String? get lastQuality => _prefs.getString('quality');

  set lastQuality(String? value) => value == null
      ? _prefs.remove('quality')
      : _prefs.setString('quality', value);
}
