import 'dart:io';

import 'package:flutter/material.dart';
import 'package:webview_flutter/webview_flutter.dart';

import '../services/account.dart';
import '../ui/theme.dart';

/// YouTube's own sign-in page. Once it lands back on YouTube signed in, the
/// login is saved (Account) and the screen closes.
class SignInScreen extends StatefulWidget {
  const SignInScreen({super.key, required this.account});

  final Account account;

  @override
  State<SignInScreen> createState() => _SignInScreenState();
}

class _SignInScreenState extends State<SignInScreen> {
  static const _start =
      'https://accounts.google.com/ServiceLogin?service=youtube&uilel=3'
      '&passive=true&continue=https%3A%2F%2Fwww.youtube.com%2Fsignin%3F'
      'action_handle_signin%3Dtrue%26app%3Dm%26next%3Dhttps%253A%252F%252F'
      'm.youtube.com%252F';

  // Google turns away sign-ins from pages it thinks are inside an app:
  // present as the phone's normal browser
  static final _browser = Platform.isIOS
      ? 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) '
            'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 '
            'Mobile/15E148 Safari/604.1'
      : 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 '
            '(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36';

  late final WebViewController _web;
  double _progress = 0;
  bool _done = false;

  @override
  void initState() {
    super.initState();
    _web = WebViewController()
      ..setJavaScriptMode(JavaScriptMode.unrestricted)
      ..setUserAgent(_browser)
      ..setNavigationDelegate(
        NavigationDelegate(
          onProgress: (value) => setState(() => _progress = value / 100),
          onPageFinished: _check,
        ),
      )
      ..loadRequest(Uri.parse(_start));
  }

  /// Back on YouTube after signing in: keep the login and close.
  Future<void> _check(String url) async {
    if (_done || !(Uri.tryParse(url)?.host.endsWith('youtube.com') ?? false)) {
      return;
    }
    if (await widget.account.saveFromWebView()) {
      _done = true;
      if (mounted) Navigator.pop(context, true);
    }
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Scaffold(
      backgroundColor: p.background,
      appBar: AppBar(
        backgroundColor: p.background,
        surfaceTintColor: Colors.transparent,
        title: const Text('Sign in to YouTube'),
        actions: [
          TextButton(
            onPressed: () async {
              final saved = await widget.account.saveFromWebView();
              if (!context.mounted) return;
              if (saved) {
                Navigator.pop(context, true);
              } else {
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(content: Text("You're not signed in yet.")),
                );
              }
            },
            child: const Text('Done'),
          ),
        ],
        bottom: PreferredSize(
          preferredSize: const Size.fromHeight(2),
          child: _progress < 1
              ? LinearProgressIndicator(value: _progress, minHeight: 2)
              : const SizedBox(height: 2),
        ),
      ),
      body: WebViewWidget(controller: _web),
    );
  }
}
