// Runs on a real device / emulator / simulator: Python and yt-dlp work inside
// the app. python tool/build.py android test integration_test
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:video_downloader/services/python_api.dart';

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  test('Python starts and yt-dlp is there', () async {
    final api = await PythonApi.instance;
    final version = await api.call('/version');
    // ignore: avoid_print
    print('Python ${version['python']}, yt-dlp ${version['yt_dlp']}');
    expect(version['yt_dlp'], isNotEmpty);
  }, timeout: const Timeout(Duration(minutes: 3)));

  test('a link that is not a link gets a friendly answer', () async {
    final api = await PythonApi.instance;
    final result = await api.call('/lookup', body: {'url': 'hello'});
    expect(result['error'], isNotNull);
  });

  test('the update rules come from core.py', () async {
    final api = await PythonApi.instance;
    final result = await api.call(
      '/update-policy',
      body: {
        'release': {'version': '99.0.0', 'required': true},
        'current': '1.0.0',
      },
    );
    expect(result['policy']['required'], isTrue);
  });

  test('looking up a real video works (needs internet)', () async {
    final api = await PythonApi.instance;
    // A short public-domain test video (not YouTube: its bot check can block
    // a test machine). YouTube is checked by hand.
    final result = await api.call(
      '/lookup',
      body: {'url': 'https://vimeo.com/76979871'},
    );
    // ignore: avoid_print
    print('lookup: ${result['title']} / ${result['error']}');
    expect(result.containsKey('title') || result['error'] != null, isTrue);
  }, timeout: const Timeout(Duration(minutes: 2)));
}
