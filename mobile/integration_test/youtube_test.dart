// Runs on a real device / simulator: YouTube's JavaScript puzzles are solved
// on the phone's own JavaScript engine (JavaScriptCore / JavaScriptSandbox),
// so yt-dlp can use YouTube's full players (and a signed-in account).
//   python tool/build.py ios --skip-package test integration_test/youtube_test.dart
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:video_downloader/services/js_engine.dart';
import 'package:video_downloader/services/python_api.dart';

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  setUpAll(() {
    PythonApi.extras = () async => {'js': await JsEngine.instance.endpoint()};
  });

  test("the phone's JavaScript engine runs a script", () async {
    final output = await JsEngine.run(
      'var parts = ["ab", "c"].map(function (s) { return s.toUpperCase(); });\n'
      'Object.assign(globalThis, {joined: parts.join("")});\n'
      'console.log(JSON.stringify({type: "ok", value: joined + (40 + 2)}));',
    );
    expect(output, '{"type":"ok","value":"ABC42"}');
  });

  test('a script error is reported, not hidden', () async {
    await expectLater(
      JsEngine.run('throw new Error("broken on purpose");'),
      throwsA(anything),
    );
  });

  test(
    'YouTube: puzzles solved on the phone, video found (needs internet)',
    () async {
      final api = await PythonApi.instance;
      final before = (await api.call('/version'))['js_runs'] as int;

      // "Me at the zoo": the first YouTube video, short and always there
      final result = await api.call(
        '/lookup',
        body: {'url': 'https://www.youtube.com/watch?v=jNQXAC9IVRw'},
        timeout: const Duration(minutes: 3),
      );
      final after = (await api.call('/version'))['js_runs'] as int;
      // ignore: avoid_print
      print(
        'YouTube lookup: ${result['title']} / ${result['error']} / '
        'qualities ${result['qualities']} / puzzles solved: ${after - before}',
      );

      final error = result['error'] as String?;
      if (error != null && error.contains('not a robot')) {
        // The test machine's network is blocked by YouTube's bot check
        markTestSkipped('YouTube blocked this network: $error');
        return;
      }
      expect(error, isNull);
      expect(result['qualities'], isNotEmpty);
      expect(after, greaterThan(before), reason: 'the phone engine was used');
    },
    timeout: const Timeout(Duration(minutes: 4)),
  );
}
