import 'dart:convert';
import 'dart:io';
import 'dart:math';

import 'package:flutter/services.dart';

/// Runs YouTube's JavaScript puzzles for yt-dlp (python/phone_js.py) on the
/// phone's own JavaScript engine: JavaScriptCore on iPhone, JavaScriptSandbox
/// on Android (native code: AppDelegate.swift / NativePlugin.kt).
///
/// Python reaches it through a small server on 127.0.0.1 that only answers
/// requests carrying this launch's random token.
class JsEngine {
  JsEngine._();

  static final instance = JsEngine._();
  static const _native = MethodChannel('video_downloader/native');

  HttpServer? _server;
  final String _token = _randomToken();

  /// Where Python sends the scripts ({port, token}); null if it can't start.
  Future<Map<String, Object>?> endpoint() async {
    try {
      final server = _server ??= await _start();
      return {'port': server.port, 'token': _token};
    } catch (_) {
      return null; // yt-dlp then uses the one YouTube player without puzzles
    }
  }

  Future<HttpServer> _start() async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    server.listen(_answer);
    return server;
  }

  Future<void> _answer(HttpRequest request) async {
    final response = request.response;
    try {
      if (request.method != 'POST' ||
          request.uri.path != '/js' ||
          request.headers.value('X-Token') != _token) {
        response.statusCode = HttpStatus.forbidden;
        return;
      }
      final code = await utf8.decodeStream(request);
      final output = await run(code);
      response
        ..statusCode = HttpStatus.ok
        ..headers.contentType = ContentType.text
        ..write(output);
    } catch (error) {
      response
        ..statusCode = HttpStatus.internalServerError
        ..write('$error');
    } finally {
      await response.close();
    }
  }

  /// Runs [code] and returns everything it printed with console.log.
  /// (The engines have no console: give it one that collects the output,
  /// and hand the collected text back as the script's last value.)
  static Future<String> run(String code) async {
    final script = StringBuffer()
      ..writeln("var __out = '';")
      ..writeln(
        'var console = {log: function (s) { __out += String(s); }, '
        'warn: function () {}, error: function () {}, info: function () {}};',
      )
      ..writeln(code)
      ..write('\n;__out');
    final output = await _native.invokeMethod<String>('evalJs', {
      'code': script.toString(),
    });
    return output ?? '';
  }

  static String _randomToken() {
    final random = Random.secure();
    return List.generate(
      32,
      (_) => random.nextInt(16).toRadixString(16),
    ).join();
  }
}
