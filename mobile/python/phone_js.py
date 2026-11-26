"""YouTube's JavaScript challenges, solved on the phone's own JavaScript engine.

YouTube hides its video links behind small JavaScript puzzles. yt-dlp solves
them with a JavaScript program (deno on the desktop app). A phone can't run
deno, but it has a JavaScript engine built in (JavaScriptCore on iPhone,
JavaScriptSandbox on Android). This yt-dlp plugin sends the puzzle script to
the app (a local server only the app knows: port + token), which runs it there
and sends back what it printed.

Without it yt-dlp can only use the one YouTube player that needs no
JavaScript, and that player can't use a signed-in account, so YouTube's
"confirm you're not a bot" check couldn't be passed.
"""

import urllib.request

from yt_dlp.extractor.youtube.jsc._builtin.ejs import EJSBaseJCP
from yt_dlp.extractor.youtube.jsc.provider import (
    JsChallengeProviderError,
    register_preference,
    register_provider,
)
from yt_dlp.extractor.youtube.pot._provider import BuiltinIEContentProvider
from yt_dlp.utils._jsruntime import JsRuntimeInfo

# (port, token) of the app's JavaScript server, set by the bridge for each
# request (the app may have restarted since Python started); None: no engine
endpoint = None

# How many puzzle scripts the phone ran (tests check it's really used)
runs = 0


@register_provider
class PhoneJCP(EJSBaseJCP, BuiltinIEContentProvider):
    PROVIDER_NAME = "phone"
    JS_RUNTIME_NAME = "phone"

    @property
    def runtime_info(self):
        if endpoint is None:
            return None
        return JsRuntimeInfo(
            name="phone", path="", version="1", version_tuple=(1,), supported=True
        )

    def _run_js_runtime(self, stdin, /):
        global runs
        port, token = endpoint
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/js",
            data=stdin.encode("utf-8"),
            headers={"X-Token": token, "Content-Type": "text/plain; charset=utf-8"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                output = response.read().decode("utf-8")
        except Exception as error:
            raise JsChallengeProviderError(
                f"The phone's JavaScript engine failed: {error}"
            ) from error
        runs += 1
        return output


@register_preference(PhoneJCP)
def preference(provider, requests):
    return 1000  # the only engine there is on a phone
