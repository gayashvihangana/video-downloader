import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:path_provider/path_provider.dart';
import 'package:webview_flutter/webview_flutter.dart';

/// The YouTube login, kept on this phone only: the sign-in page's cookies,
/// saved in the format yt-dlp reads (Netscape cookies.txt). With them,
/// YouTube's "confirm you're not a bot" check lets the app through.
class Account extends ChangeNotifier {
  Account._(this.cookiesPath);

  static const _native = MethodChannel('video_downloader/native');

  /// Cookies only a signed-in YouTube account has.
  static const loginCookies = {'SAPISID', '__Secure-3PAPISID', 'LOGIN_INFO'};

  final String cookiesPath;

  static Future<Account> load() async {
    final dir = await getApplicationSupportDirectory();
    return Account._('${dir.path}/youtube_cookies.txt');
  }

  bool get signedIn => File(cookiesPath).existsSync();

  /// After the sign-in page: keep the login if there is one.
  Future<bool> saveFromWebView() async {
    final raw = await _native.invokeListMethod<Map>('youtubeCookies') ?? [];
    final cookies = [
      for (final cookie in raw) Map<String, Object?>.from(cookie),
    ];
    if (!cookies.any((c) => loginCookies.contains(c['name']))) return false;

    await File(cookiesPath).writeAsString(toNetscape(cookies), flush: true);
    notifyListeners();
    return true;
  }

  Future<void> signOut() async {
    final file = File(cookiesPath);
    if (await file.exists()) await file.delete();
    await WebViewCookieManager().clearCookies();
    notifyListeners();
  }

  /// cookies.txt: one line per cookie, tab-separated.
  @visibleForTesting
  static String toNetscape(
    List<Map<String, Object?>> cookies, {
    DateTime? now,
  }) {
    // Session cookies get half a year, so yt-dlp doesn't drop them
    final later =
        (now ?? DateTime.now())
            .add(const Duration(days: 180))
            .millisecondsSinceEpoch ~/
        1000;
    final lines = ['# Netscape HTTP Cookie File'];
    for (final cookie in cookies) {
      var domain = '${cookie['domain'] ?? '.youtube.com'}';
      if (!domain.startsWith('.')) domain = '.$domain';
      final expires = (cookie['expires'] as num?)?.toInt() ?? 0;
      lines.add(
        [
          domain,
          'TRUE',
          '${cookie['path'] ?? '/'}',
          cookie['secure'] == false ? 'FALSE' : 'TRUE',
          '${expires > 0 ? expires : later}',
          '${cookie['name']}',
          '${cookie['value']}',
        ].join('\t'),
      );
    }
    return '${lines.join('\n')}\n';
  }
}
