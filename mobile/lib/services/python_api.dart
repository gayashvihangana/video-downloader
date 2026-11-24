import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';

import 'package:path_provider/path_provider.dart';
import 'package:serious_python/serious_python.dart';

/// The Python side of the app (mobile/python/bridge.py): yt-dlp and the
/// desktop app's core.py, reached over HTTP on 127.0.0.1.
///
/// Python runs once per app process. Only this app can use it: every request
/// carries a random token that Python got when it started.
class PythonApi {
  PythonApi._(this._port, this._token);

  final int _port;
  final String _token;
  final HttpClient _http = HttpClient()
    ..connectionTimeout = const Duration(seconds: 5);

  static PythonApi? _instance;
  static Future<PythonApi>? _starting;

  /// The running API (started on first use).
  static Future<PythonApi> get instance {
    final ready = _instance;
    if (ready != null) return Future.value(ready);
    return _starting ??= _start().then((api) => _instance = api);
  }

  static Future<PythonApi> _start() async {
    final dir = await getApplicationSupportDirectory();
    final portFile = File('${dir.path}/python_port');
    final tokenFile = File('${dir.path}/python_token');

    // Python outlives a Flutter hot restart: reuse it if it still answers
    if (await portFile.exists() && await tokenFile.exists()) {
      final api = PythonApi._(
        int.parse(await portFile.readAsString()),
        await tokenFile.readAsString(),
      );
      if (await api._answers()) return api;
    }

    final token = _randomToken();
    if (await portFile.exists()) await portFile.delete();
    await tokenFile.writeAsString(token, flush: true);

    // Runs main.py on a background thread; returns at once
    unawaited(
      SeriousPython.run(
        appFileName: 'main.py',
        environmentVariables: {
          'VD_TOKEN': token,
          'VD_PORT_FILE': portFile.path,
        },
      ),
    );

    // The first launch unpacks Python, which takes a while on slow phones
    final deadline = DateTime.now().add(const Duration(seconds: 90));
    while (DateTime.now().isBefore(deadline)) {
      if (await portFile.exists()) {
        final api = PythonApi._(
          int.parse(await portFile.readAsString()),
          token,
        );
        if (await api._answers()) return api;
      }
      await Future<void>.delayed(const Duration(milliseconds: 200));
    }
    throw const PythonApiException("The app's download engine didn't start.");
  }

  static String _randomToken() {
    final random = Random.secure();
    return List.generate(
      32,
      (_) => random.nextInt(16).toRadixString(16),
    ).join();
  }

  Future<bool> _answers() async {
    try {
      await call('/version', timeout: const Duration(seconds: 3));
      return true;
    } catch (_) {
      return false;
    }
  }

  /// POST a JSON request; returns the JSON answer.
  Future<Map<String, dynamic>> call(
    String path, {
    Map<String, dynamic> body = const {},
    Duration timeout = const Duration(seconds: 90),
  }) async {
    final request = await _http
        .post(InternetAddress.loopbackIPv4.address, _port, path)
        .timeout(timeout);
    // Python's simple server reads exactly Content-Length bytes and closes
    // the connection after each answer, so send the length (not chunked)
    // and don't keep the connection open
    final data = utf8.encode(jsonEncode(body));
    request
      ..persistentConnection = false
      ..contentLength = data.length;
    request.headers
      ..contentType = ContentType.json
      ..set('X-Token', _token);
    request.add(data);

    final response = await request.close().timeout(timeout);
    final text = await response.transform(utf8.decoder).join().timeout(timeout);

    if (response.statusCode == 403) {
      throw const PythonApiException('Not allowed.');
    }
    final answer = jsonDecode(text) as Map<String, dynamic>;
    if (response.statusCode != 200) {
      throw PythonApiException(
        answer['error'] as String? ?? 'Something went wrong.',
      );
    }
    return answer;
  }
}

class PythonApiException implements Exception {
  const PythonApiException(this.message);
  final String message;

  @override
  String toString() => message;
}
