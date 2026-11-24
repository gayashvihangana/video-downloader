import 'package:flutter/services.dart';

/// Links shared to the app ("Share -> Video Downloader"), Android only.
/// On iPhone the user pastes the link (the app offers a copied link).
class ShareReceiver {
  static const _channel = MethodChannel('video_downloader/share');

  /// Calls [onLink] for the link the app was opened with, and for every
  /// link shared while it's open.
  static Future<void> listen(void Function(String link) onLink) async {
    _channel.setMethodCallHandler((call) async {
      if (call.method == 'shared' && call.arguments is String) {
        onLink(call.arguments as String);
      }
    });

    try {
      final initial = await _channel.invokeMethod<String>('initialLink');
      if (initial != null && initial.isNotEmpty) onLink(initial);
    } on MissingPluginException {
      // iPhone: no share channel
    }
  }
}
