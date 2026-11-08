import threading
import http.cookiejar

import requests

APP_VERSION = "40.2.0"
BWV = "BytedanceWebview/d8a21c6"

PROFILE = dict(av="13", model="Pixel 7", build="TQ3A.230901.001", chrome="131.0.6778.135", brand="google")


def profile_for(key: str = "") -> dict:
    return dict(PROFILE)


def mini_ua(profile: dict = None, app_version: str = APP_VERSION, tma_ver: str = APP_VERSION) -> str:
    p = profile or PROFILE
    return (f"Mozilla/5.0 (Linux; Android {p['av']}; {p['model']} Build/{p['build']}; wv) "
            f"AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/{p['chrome']} "
            f"Mobile Safari/537.36 {BWV} ToutiaoMicroApp/{tma_ver} "
            f"AppName/aweme_lite app_version/{app_version}")


class _NoCookies(http.cookiejar.CookiePolicy):
    netscape = True
    rfc2965 = hide_cookie2 = False

    def set_ok(self, cookie, request):
        return False

    def return_ok(self, cookie, request):
        return False

    def domain_return_ok(self, domain, request):
        return False

    def path_return_ok(self, path, request):
        return False


class MiniSession:
    def __init__(self):
        self._tl = threading.local()

    def _sess(self):
        s = getattr(self._tl, "s", None)
        if s is None:
            s = requests.Session()
            s.cookies.set_policy(_NoCookies())
            self._tl.s = s
        return s

    def get(self, url, **kw):
        return self._sess().get(url, **kw)

    def post(self, url, **kw):
        return self._sess().post(url, **kw)

    def request(self, method, url, **kw):
        return self._sess().request(method, url, **kw)

    def close(self):
        s = getattr(self._tl, "s", None)
        if s is not None:
            try:
                s.close()
            except Exception:
                pass


def new_session():
    return MiniSession()
