import sys, os, time, json, re, argparse, subprocess, threading, hashlib, uuid
try:
    import requests, urllib3
    urllib3.disable_warnings()
except ImportError:
    sys.exit("Cần: pip install requests")
from sources import SOURCES, ADAPTERS, auth_app, app_version
import dy_transport as DYT

sys.stdout.reconfigure(encoding="utf-8")


def _pad_key(token):
    t = token or ""
    return (t.ljust(32, "0") if len(t) < 32 else t[:32]).encode("utf-8")


def _duolai_iv(deviceid, ts):
    return bytes.fromhex(hashlib.md5(f"{deviceid}{ts}".encode("utf-8")).hexdigest())


def _aes_cbc(key, iv):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    return Cipher(algorithms.AES(key), modes.CBC(iv))


def duolai_encrypt(payload, token, deviceid, ts):
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    p = 16 - (len(data) % 16)
    enc = _aes_cbc(_pad_key(token), _duolai_iv(deviceid, ts)).encryptor()
    return (enc.update(data + bytes([p]) * p) + enc.finalize()).hex()


def duolai_decrypt(hex_str, token, deviceid, ts):
    dec = _aes_cbc(_pad_key(token), _duolai_iv(deviceid, ts)).decryptor()
    raw = dec.update(bytes.fromhex(hex_str)) + dec.finalize()
    return (raw[:-raw[-1]] if raw else raw).decode("utf-8", "ignore")


def _load_device_id():
    p = os.path.join(os.path.dirname(__file__), "deviceid.local.txt")
    try:
        if os.path.exists(p):
            v = open(p).read().strip()
            if v:
                return v
        v = str(uuid.uuid4())
        open(p, "w").write(v)
        return v
    except Exception:
        return str(uuid.uuid4())


def _discover_alt_cookies(primary=""):
    import glob
    out = []
    try:
        d = os.path.dirname(os.path.abspath(__file__))
        for p in sorted(glob.glob(os.path.join(d, "cookie*.local.txt"))):
            base = os.path.basename(p).lower()
            if base == "cookie.local.txt" or ".bak" in base:
                continue
            c = load_cookie(p)
            if c and c != primary and c not in out:
                out.append(c)
    except Exception:
        pass
    return out


TOKEN_TTL = 12 * 3600


def _appn(app_id):
    for s in SOURCES:
        if s["app_id"] == app_id:
            return s["name"]
    return (app_id or "?")[:12]


def _load_token_cache(path):
    try:
        d = json.load(open(path, encoding="utf-8"))
        now = time.time()
        return {k: v for k, v in d.items()
                if isinstance(v, dict) and v.get("token") and now - v.get("ts", 0) < TOKEN_TTL}
    except Exception:
        return {}


def _acct_key(cookie):
    m = re.search(r"uid_tt=([0-9a-zA-Z]+)", cookie or "")
    if m:
        return "u" + m.group(1)[:20]
    m = re.search(r"sessionid=([0-9a-zA-Z]+)", cookie or "")
    if m:
        return "s" + m.group(1)[:20]
    return "h" + hashlib.md5((cookie or "").encode()).hexdigest()[:12]


def _acct_device(key):
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, "duolai-dev-" + key))


class AccountPool:

    def __init__(self, cookies):
        self.accounts, seen = [], set()
        for ck in (cookies or []):
            if not ck:
                continue
            k = _acct_key(ck)
            if k in seen:
                continue
            seen.add(k)
            self.accounts.append({"key": k, "cookie": ck, "device": _acct_device(k)})
        self._rr = {}
        self._lock = threading.Lock()

    def next(self, app):
        with self._lock:
            if not self.accounts:
                return None
            i = self._rr.get(app, 0) % len(self.accounts)
            self._rr[app] = i + 1
            return self.accounts[i]


def load_cookie(path):
    if not path or not os.path.exists(path):
        return ""
    txt = open(path, encoding="utf-8").read().strip()
    if txt[:1] in "{[":
        j = json.loads(txt)
        ck = j["cookies"] if isinstance(j, dict) and "cookies" in j else j
        return "; ".join(f"{c['name']}={c['value']}" for c in ck)
    return txt


def find_source(key):
    for s in SOURCES:
        if key in (s["name"], s["app_id"]) or key.lower() == s["app_id"].lower():
            return s
    if key.startswith(("tt", "ks")):
        return {"name": key, "app_id": key, "adapter": "duolai", "status": "adhoc"}
    raise SystemExit(f"Không thấy nguồn '{key}'. Xem `python miniapp_client.py list`.")


class Client:
    def __init__(self, cookie="", token="", timeout=15, retries=3, alt_cookies=None,
                 dl_workers=2, verbose=True):
        self.cookie, self.token = cookie, token
        self.cookies = [c for c in ([cookie] + list(alt_cookies or []) + _discover_alt_cookies(cookie)) if c]
        self.pool = AccountPool(self.cookies)
        self.device_id = _load_device_id()
        self.tokens = {}
        self._acct_tokens = {}
        self._tok_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "token_cache.local.json")
        self._tok_disk = _load_token_cache(self._tok_path)
        self._tok_src = {}
        self._app_alias = {}
        self.mint_errors = {}
        self.token_cookie = {}
        self.borrowed_catalog = {}
        self._detail_cache = {}
        self.timeout, self.retries = timeout, retries
        self.s = DYT.new_session()
        self.dl_s = requests.Session()
        self.dl_workers = dl_workers
        self._pl_cache = {}
        self.verbose = verbose
        self.log(f"● Client sẵn sàng · {len(self.pool.accounts)} account")

    def log(self, msg):
        if self.verbose:
            try:
                print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)
            except Exception:
                pass

    def _headers(self, app_id, token="", use_cookie=False, cookie=None):
        ck = cookie if cookie is not None else self.cookie
        prof = DYT.profile_for(_acct_key(ck) if ck else app_id)
        h = {
            "token": token, "deviceid": "", "fingerprint": "",
            "timestamp": str(int(time.time()) - 5), "content-type": "application/json",
            "koc-session": "", "applet-deviceid": "", "applet-uid": "",
            "koc-referer": f"https://tmaservice.developer.toutiao.com/?appid={app_id}&version={app_version(app_id)}",
            "User-Agent": DYT.mini_ua(prof),
        }
        if use_cookie and ck:
            h["Cookie"] = ck
        return h

    def _get(self, adapter, ep_key, app_id, params, token="", use_cookie=False):
        a = ADAPTERS[adapter]
        url = f"https://{a['host']}{a['base']}{a[ep_key]}"
        last = None
        for _ in range(self.retries):
            try:
                r = self.s.get(url, headers=self._headers(app_id, token, use_cookie), params=params,
                               timeout=self.timeout, verify=False)
                try:
                    return r.status_code, r.json()
                except Exception:
                    return r.status_code, {"_raw": r.text[:200]}
            except Exception as e:
                last = e
        return 0, {"_err": str(last)}

    def _signed_get(self, ep_key, app_id, params, acct, token):
        a = ADAPTERS["duolai"]
        url = f"https://{a['host']}{a['base']}{a[ep_key]}"
        dev, ck = acct["device"], acct["cookie"]
        last = None
        for _ in range(self.retries):
            ts = str(int(time.time()) - 5)
            h = self._headers(app_id, token, use_cookie=True, cookie=ck)
            h["deviceid"] = dev
            h["fingerprint"] = dev
            h["timestamp"] = ts
            try:
                r = self.s.get(url, headers=h, params={"l_sign": duolai_encrypt(params, token, dev, ts)},
                               timeout=self.timeout, verify=False)
                try:
                    j = r.json()
                except Exception:
                    return r.status_code, {"_raw": r.text[:200]}
                if isinstance(j, dict) and "r_sign" in j:
                    try:
                        j = json.loads(duolai_decrypt(j["r_sign"], token, dev, ts))
                    except Exception as e:
                        return r.status_code, {"_decfail": str(e)}
                return r.status_code, j
            except Exception as e:
                last = e
        return 0, {"_err": str(last)}

    def _tok_save(self):
        try:
            json.dump(self._tok_disk, open(self._tok_path, "w", encoding="utf-8"))
        except Exception:
            pass

    def _tok_forget(self, key, app_id):
        self._acct_tokens.pop((key, app_id), None)
        if self._tok_disk.pop(f"{key}:{app_id}", None) is not None:
            self._tok_save()

    def _mint_slots(self, app_id):
        realm = auth_app(app_id)
        slots = [] if app_id.startswith("ks") else [app_id]
        if realm != app_id and realm not in slots:
            slots.append(realm)
        return slots or [realm]

    def _tok_slot(self, key, app_id):
        for slot in self._mint_slots(app_id):
            if (key, slot) in self._acct_tokens:
                return slot
            ent = self._tok_disk.get(f"{key}:{slot}")
            if ent and time.time() - ent.get("ts", 0) < TOKEN_TTL:
                return slot
        return None

    def _mint_for(self, acct, app_id, force=False):
        key = acct["key"]
        slots = self._mint_slots(app_id)
        if not force:
            for slot in slots:
                if (key, slot) in self._acct_tokens:
                    return self._acct_tokens[(key, slot)]
                ent = self._tok_disk.get(f"{key}:{slot}")
                if ent and time.time() - ent.get("ts", 0) < TOKEN_TTL:
                    self._acct_tokens[(key, slot)] = ent["token"]
                    self._tok_src[(key, slot)] = "cache"
                    return ent["token"]
        last = ""
        for slot in slots:
            tok, msg = self._mint_one(app_id, acct["cookie"], login_appid=slot)
            last = msg
            if tok:
                self._acct_tokens[(key, slot)] = tok
                self._tok_src[(key, slot)] = "fresh"
                self._tok_disk[f"{key}:{slot}"] = {"token": tok, "ts": time.time()}
                self._tok_save()
                self.mint_errors.pop(app_id, None)
                return tok
            self._tok_forget(key, slot)
            if "cookie hết hạn" in str(msg) or str(msg).startswith("net:"):
                break
        self.mint_errors[app_id] = str(last)
        return None

    def _duolai_alts(self, app_id):
        cands = []
        for s_ in SOURCES:
            a = s_.get("app_id", "")
            if s_.get("adapter") != "duolai" or not a.startswith("tt") or a == app_id:
                continue
            if a in [c[1] for c in cands]:
                continue
            cached = any(f"{acc['key']}:{slot}" in self._tok_disk
                         for acc in self.pool.accounts for slot in self._mint_slots(a))
            cands.append((0 if cached else 1, a))
        cands.sort()
        out = [a for _, a in cands]
        prev = self._app_alias.get(app_id)
        if prev in out:
            out.remove(prev)
            out.insert(0, prev)
        return out

    @staticmethod
    def _has_payload(j):
        if not isinstance(j, dict):
            return False
        d = j.get("data")
        if isinstance(d, list):
            return bool(d)
        if isinstance(d, dict):
            return bool(d)
        return False

    def _authed_duolai(self, ep_key, app_id, params):
        st, j = self._authed_duolai_one(ep_key, app_id, params)
        if ep_key not in ("playlist", "playurl", "detail"):
            return st, j
        failed = isinstance(j, dict) and j.get("_err")
        if not failed and self._has_payload(j):
            return st, j
        best = (st, j)
        for alt in self._duolai_alts(app_id):
            st2, j2 = self._authed_duolai_one(ep_key, alt, params)
            if not (isinstance(j2, dict) and j2.get("_err")) and self._has_payload(j2):
                self._app_alias[app_id] = alt
                self.mint_errors.pop(app_id, None)
                self.log(f"↪ «{_appn(app_id)}» không có dữ liệu → dùng «{_appn(alt)}» (id dùng chung)")
                return st2, j2
            if failed and isinstance(j2, dict) and not j2.get("_err"):
                best = (st2, j2)
        return best

    def _authed_duolai_one(self, ep_key, app_id, params):
        if self.tokens.get(app_id):
            acct = {"key": "manual", "cookie": self.cookie, "device": self.device_id}
            return self._signed_get(ep_key, app_id, params, acct, self.tokens[app_id])
        n = max(1, len(self.pool.accounts))
        for _ in range(n):
            acct = self.pool.next(app_id)
            if acct is None:
                break
            key = acct["key"]
            tok = self._mint_for(acct, app_id)
            if not tok:
                continue
            st, j = self._signed_get(ep_key, app_id, params, acct, tok)
            code = j.get("code") if isinstance(j, dict) else None
            self.log(f"→ {ep_key} «{_appn(app_id)}» acct {key[:9]} · code={code if code is not None else '?'}")
            slot = self._tok_slot(key, app_id) or app_id
            if code == 401 and self._tok_src.get((key, slot)) == "cache":
                self._tok_forget(key, slot)
                tok = self._mint_for(acct, app_id, force=True)
                if tok:
                    st, j = self._signed_get(ep_key, app_id, params, acct, tok)
                    code = j.get("code") if isinstance(j, dict) else None
                    slot = self._tok_slot(key, app_id) or app_id
            if code == 401:
                self._tok_forget(key, slot)
                msg = (j.get("msg") or j.get("message") or "") if isinstance(j, dict) else ""
                self.mint_errors[app_id] = str(msg) or "401"
                self.log(f"✗ «{_appn(app_id)}» acct {key[:9]}: {str(msg)[:34] or '401'} → thử account kế")
                continue
            self.token_cookie[app_id] = acct["cookie"]
            return st, j
        return 0, {"_err": self._token_error({"app_id": app_id, "name": app_id})}

    def _mint_one(self, app_id, cookie, login_appid=None):
        realm = login_appid or app_id
        try:
            tag = _appn(app_id) + (f" (mint qua chủ hộ {_appn(realm)})" if realm != app_id else "")
            self.log(f"🔑 MINT «{tag}» acct {_acct_key(cookie)[:9]}…")
            prof = DYT.profile_for(_acct_key(cookie))
            ua = DYT.mini_ua(prof)
            r = self.s.get("https://ma.zijieapi.com/api/apps/v3/login",
                           params={"aid": 1128, "appid": realm},
                           headers={"User-Agent": ua, "Cookie": cookie},
                           timeout=self.timeout, verify=False)
            d = (r.json() or {}).get("data") or {}
            code = d.get("code") or d.get("anonymousCode")
            if not code:
                return None, "cookie hết hạn (không lấy được js_code lẫn anonymousCode)"
            h = {"content-type": "application/json", "User-Agent": ua,
                 "koc-referer": f"https://tmaservice.developer.toutiao.com/?appid={realm}"
                                f"&version={app_version(realm)}",
                 "Cookie": cookie, "timestamp": str(int(time.time()))}
            j = self.s.post("https://xiaoguofanxing.lizhibj.cn/api/duolai/login", headers=h,
                            json={"provider": "toutiao", "js_code": code, "appid": realm, "system": "AND"},
                            timeout=self.timeout, verify=False).json()
            tok = (j.get("data") or {}).get("token", "")
            reason = j.get("msg") or j.get("message") or ("OK" if tok else "no token")
            self.log(f"🔑 MINT «{_appn(realm)}» → {'THÀNH CÔNG' if tok else 'HỎNG: ' + str(reason)[:40]}")
            return (tok or None), reason
        except Exception as e:
            self.log(f"🔑 MINT «{_appn(app_id)}» → lỗi mạng: {type(e).__name__}")
            return None, f"net: {type(e).__name__}"

    def mint_token(self, app_id):
        if self.tokens.get(app_id):
            return self.tokens[app_id]
        if app_id.startswith("ks") and auth_app(app_id) == app_id:
            return self.token
        n = max(1, len(self.pool.accounts))
        for _ in range(n):
            acct = self.pool.next(app_id)
            if acct is None:
                break
            tok = self._mint_for(acct, app_id)
            if tok:
                self.token_cookie[app_id] = acct["cookie"]
                return tok
        return self.token

    def check_cookie_alive(self, cookie, app_id="tt839df5201ca3d79a01"):
        try:
            prof = DYT.profile_for(_acct_key(cookie))
            ua = DYT.mini_ua(prof)
            r = self.s.get("https://ma.zijieapi.com/api/apps/v3/login",
                           params={"aid": 1128, "appid": app_id},
                           headers={"User-Agent": ua, "Cookie": cookie},
                           timeout=self.timeout, verify=False)
            return bool(((r.json() or {}).get("data") or {}).get("code"))
        except Exception:
            return None

    def browse(self, src, page=1, pagesize=18):
        p = {"system": "AND", "page": page, "pagesize": pagesize}
        st, j = self._get(src["adapter"], "browse", src["app_id"], p)
        d = j.get("data") if isinstance(j, dict) else None
        n = len(d) if isinstance(d, list) else (len(d.get("list", [])) if isinstance(d, dict) else 0)
        if n:
            self.borrowed_catalog.pop(src["app_id"], None)
        if n == 0:
            st2, j2 = self._get(src["adapter"], "list", src["app_id"], p)
            d2 = j2.get("data") if isinstance(j2, dict) else None
            if (d2 if isinstance(d2, list) else (d2.get("list") if isinstance(d2, dict) else None)):
                return st2, j2
            for alt in self._duolai_alts(src["app_id"])[:2]:
                st3, j3 = self._get("duolai", "browse", alt, p)
                d3 = j3.get("data") if isinstance(j3, dict) else None
                l3 = d3 if isinstance(d3, list) else ((d3 or {}).get("list") if isinstance(d3, dict) else None)
                if l3:
                    j3["_borrowed"] = alt
                    self.borrowed_catalog[src["app_id"]] = alt
                    return st3, j3
        return st, j

    def discover(self, src, mode="recommend", pagesize=24, page=1):
        if src["adapter"] != "duolai":
            return "unsupported", []
        if mode == "category":
            return "unavailable", []
        if mode == "banner":
            tok = self.mint_token(src["app_id"])
            _, j = self._get("duolai", "banner", src["app_id"], {"system": "AND"}, token=tok, use_cookie=True)
            out = []
            for b in (j.get("data") or []):
                cid = b.get("collection_id") or b.get("video_id")
                if not cid:
                    continue
                title = b.get("title") or b.get("name") or ""
                out.append({"id": cid, "name": title or f"Nổi bật #{cid}",
                            "cover_url": b.get("cover_url") or b.get("url"),
                            "collection_info": {"collection_id": cid}})
            return "ok", out
        if page >= 2:
            _, j = self._get("duolai", "list", src["app_id"],
                             {"system": "AND", "page": page - 1, "pagesize": pagesize})
            d = j.get("data") if isinstance(j, dict) else None
            items = d if isinstance(d, list) else ((d or {}).get("list") if isinstance(d, dict) else []) or []
            return "ok", items
        items = []
        for _ in range(2):
            _, j = self.browse(src, page=1, pagesize=pagesize)
            items = j.get("data") if isinstance(j.get("data"), list) else []
            if items:
                break
        if mode == "hot":
            items = sorted(items, key=lambda x: -(x.get("watch_num") or 0))
        elif mode == "new":
            items = sorted(items, key=lambda x: (x.get("watch_num") or 0))
        return "ok", items

    def check(self, src, attempts=3):
        best, lastcode = 0, None
        for _ in range(attempts):
            code, j = self.browse(src)
            if code == 0:
                continue
            lastcode = j.get("code")
            if lastcode in (401, 500):
                return False, f"cần token (code={lastcode})"
            data = j.get("data")
            n = len(data) if isinstance(data, list) else (len(data.get("list", [])) if isinstance(data, dict) else 0)
            best = max(best, n)
            if best > 0:
                return True, f"lên · {best} phim"
        _, lj = self._get("duolai", "list", src["app_id"], {"system": "AND", "page": 1, "pagesize": 20})
        ld = lj.get("data") if isinstance(lj, dict) else None
        ln = len(ld) if isinstance(ld, list) else (len(ld.get("list", [])) if isinstance(ld, dict) else 0)
        if ln:
            return True, f"lên · {ln} phim"
        return (False, "NET-ERR") if lastcode is None else (False, "server ok nhưng rỗng")

    def _tok(self, src):
        return self.mint_token(src["app_id"])

    def playlist(self, src, collection_id, page=1, pagesize=60):
        if (src["app_id"], str(collection_id)) in self._pl_cache:
            cached = self._pl_cache[(src["app_id"], str(collection_id))]
            self.log(f"📃 playlist «{_appn(src['app_id'])}» cid {collection_id}: {len(cached)} tập (CACHE)")
            return 200, {"data": cached}
        acc, seen = [], set()
        first = None
        pg = page
        psize = max(400, pagesize)
        for _ in range(40):
            p = {"collection_id": collection_id, "page": pg, "pagesize": psize}
            st, j = self._authed_duolai("playlist", src["app_id"], p)
            if first is None:
                first = (st, j)
            if not isinstance(j, dict) or j.get("_err"):
                break
            batch = j.get("data") if isinstance(j.get("data"), list) else []
            fresh = [e for e in batch if e.get("id") not in seen]
            for e in fresh:
                seen.add(e.get("id"))
            acc.extend(fresh)
            if len(batch) < psize:
                break
            pg += 1
        if acc:
            self._pl_cache[(src["app_id"], str(collection_id))] = acc
            self.log(f"📃 playlist «{_appn(src['app_id'])}» cid {collection_id}: {len(acc)} tập ({pg} trang)")
            return 200, {"data": acc}
        st, j = first
        if isinstance(j, dict) and not j.get("_err") and not (j.get("data") or []):
            j["_hint"] = ("Không app nào trong pool mở được bộ này. Danh sách tập là APP-SCOPED: chỉ app "
                          "CÓ bộ đó trong catalog mới trả tập (app khác trả 视频不存在或已下架). "
                          "Bộ chỉ có riêng bên Kwai thì cần token app Kwai.")
        return st, j

    def detail(self, src, collection_id):
        key = (src["app_id"], str(collection_id))
        if key in self._detail_cache:
            return self._detail_cache[key]
        out = {}
        st, j = self._authed_duolai("detail", src["app_id"], {"id": collection_id, "system": "AND"})
        d = j.get("data") if isinstance(j, dict) else None
        if isinstance(d, dict) and d:
            ci = d.get("collection_info") or {}
            tags = d.get("collection_tags") or ci.get("collection_tags") or ci.get("collection_category") or []
            out = {"name": d.get("name") or ci.get("collection_name") or "",
                   "describe": (d.get("describe") or "").strip(),
                   "tags": [t for t in tags if t],
                   "author": ci.get("collection_account_name") or "",
                   "status": ci.get("collection_completion_status") or "",
                   "total_eps": ci.get("collection_updated_num") or 0,
                   "copyright": ci.get("collection_copyright_type") or "",
                   "watch_num": d.get("watch_num") or 0,
                   "watermark": d.get("show_watermark") == "T",
                   "cover_url": d.get("cover_url") or ""}
        self._detail_cache[key] = out
        return out

    def play_url(self, src, episode_id, site="WANGSU"):
        p = {"id": episode_id, "system": "AND"}
        return self._authed_duolai("playurl", src["app_id"], p)

    def _token_error(self, src):
        app = src["app_id"]
        name = src.get("name", app)
        if app.startswith("ks") and auth_app(app) == app:
            return ("Kwai: hầu hết bộ vẫn tải được bằng token app Douyin (id dùng chung). Bộ nào rỗng "
                    "là nội dung độc quyền Kwai — cần js_code Kuaishou "
                    f"(có token thì: cli.tokens['{app}']=<token>).")
        if not self.pool.accounts:
            return "chưa có cookie nào — đặt cookie.local.txt (và cookie2.local.txt…) cạnh script."
        return f"Không lấy được token cho «{name}»: {self.mint_errors.get(app, 'không rõ')}."


def _safe(s):
    return re.sub(r'[\\/:*?"<>|]', "_", str(s)).strip() or "video"


def dl_headers_for(src, url):
    return {"User-Agent": "Mozilla/5.0"}


def download_episode(cli, src, ep, outdir, log=print, progress=None, stop=None):
    eid = ep.get("id"); seq = ep.get("sequence_no", eid)
    free = "🆓" if ep.get("is_free") in ("T", True, 1) else "🔒"
    if progress: progress(0, 0, 0, "lấy link…")
    code, pj = cli.play_url(src, eid)
    url = (pj.get("data") or {}).get("url") if isinstance(pj.get("data"), dict) else None
    if not url:
        why = pj.get("message") or pj.get("msg") or (pj.get("_err") if isinstance(pj, dict) else "") or str(pj)
        log(f"  [tập {seq} {free}] play_url lỗi: {why}")
        if progress: progress(0, 0, 0, f"LỖI: {str(why)[:160]}")
        return None
    alt = getattr(cli, "_app_alias", {}).get(src["app_id"])
    if alt:
        log(f"  [tập {seq} {free}] «{src['name']}» không có dữ liệu → dùng token app {alt} (id dùng chung)")
    out = os.path.join(outdir, f"ep{int(seq):03d}_{eid}.mp4")
    if url.split("?")[0].lower().endswith(".m3u8"):
        eff_appid = getattr(cli, "_app_alias", {}).get(src["app_id"]) or src["app_id"]
        if progress: progress(0, 0, 0, "HLS antihook…")
        return _download_hls_antihook(cli, src, url, out, eff_appid, log, progress, stop)
    tmp = out + ".part"
    for attempt in range(6):
        start = os.path.getsize(tmp) if os.path.exists(tmp) else 0
        try:
            _h = dict(dl_headers_for(src, url))
            _h["Range"] = f"bytes={start}-"
            r = cli.dl_s.get(url, headers=_h, stream=True, timeout=90, verify=False)
            status = r.status_code
            mode = "ab"
            if status == 200 and start > 0:
                start, mode = 0, "wb"
            elif status not in (200, 206):
                raise RuntimeError(f"HTTP {status}")
            total = start + int(r.headers.get("Content-Length", 0) or 0)
            done = start; t0 = time.time()
            with open(tmp, mode) as f:
                for c in r.iter_content(256 * 1024):
                    if not c: continue
                    if stop and stop():
                        if progress: progress(done, total, 0, "đã dừng")
                        return None
                    f.write(c); done += len(c)
                    if progress:
                        el = time.time() - t0
                        progress(done, total, (done - start) / el / 1024 if el > 0 else 0, "tải")
            os.replace(tmp, out)
            log(f"  [tập {seq} {free}] OK {os.path.getsize(out)//1024//1024}MB → {os.path.basename(out)}")
            if progress: progress(total or 1, total or 1, 0, "xong")
            return out
        except Exception as e:
            tag = str(e) or type(e).__name__
            log(f"  [tập {seq} {free}] lỗi lần {attempt+1}/6: {tag} (resume {start//1024//1024}MB)")
            if progress: progress(start, 0, 0, f"lỗi ({tag}), thử lại…")
            if stop and stop():
                return None
    return None


_ANTIHOOK_UA = ("Mozilla/5.0 (Linux; Android 14; 23049RAD8C Build/UKQ1.230804.001; wv) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/150.0.7871.181 "
                "Mobile Safari/537.36 aweme/37.1.0 ToutiaoMicroApp/3.98.0 PluginVersion/37109020")


def _antihook_headers(appid):
    return {"Accept": "*/*", "User-Agent": _ANTIHOOK_UA,
            "Referer": f"https://tmaservice.developer.toutiao.com/?appid={appid}&version=3.9.41",
            "X-Tt-Tag": "miniapp", "X-Tt-SubTag": f"miniapp_appid:{appid}", "Connection": "keep-alive"}


def _download_hls_antihook(cli, src, m3u8_url, out, appid, log=print, progress=None, stop=None):
    from urllib.parse import urljoin
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from Crypto.Cipher import AES
    import shutil
    H = _antihook_headers(appid)
    try:
        r = cli.dl_s.get(m3u8_url, headers=H, timeout=30, verify=False)
        if r.status_code != 200:
            log(f"  antihook m3u8 HTTP {r.status_code}"); return None
        key_url, iv = None, bytes(16)
        segs = []
        for line in r.text.strip().splitlines():
            line = line.strip()
            if line.startswith("#EXT-X-KEY"):
                m = re.search(r'URI="([^"]+)"', line)
                if m: key_url = urljoin(m3u8_url, m.group(1))
                mi = re.search(r'IV=0x([0-9a-fA-F]+)', line)
                if mi: iv = bytes.fromhex(mi.group(1).zfill(32))
            elif line and not line.startswith("#"):
                segs.append(urljoin(m3u8_url, line))
        if not segs:
            log("  antihook: playlist KHÔNG có segment (sai header -> byte mồi?)"); return None
        key = None
        if key_url:
            kr = cli.dl_s.get(key_url, headers=H, timeout=20, verify=False)
            if kr.status_code == 200 and len(kr.content) == 16:
                key = kr.content
            else:
                log(f"  antihook: lấy key hỏng HTTP {kr.status_code} len={len(kr.content)}"); return None
        total = len(segs)
        segdir = out + ".segs"; os.makedirs(segdir, exist_ok=True)
        seg_paths = [os.path.join(segdir, f"{i:05d}.bin") for i in range(total)]

        def _fetch(i):
            for attempt in range(4):
                if stop and stop(): return "stopped"
                try:
                    sr = cli.dl_s.get(segs[i], headers=H, timeout=60, verify=False)
                    if sr.status_code != 200:
                        raise RuntimeError(f"HTTP {sr.status_code}")
                    chunk = sr.content
                    if key:
                        chunk = AES.new(key, AES.MODE_CBC, iv).decrypt(chunk)
                    if i == 0 and chunk[:1] != b"\x47":
                        raise RuntimeError(f"seg0 sync={chunk[:1].hex()} (byte mồi / sai appid header)")
                    with open(seg_paths[i], "wb") as sf:
                        sf.write(chunk)
                    return True
                except Exception as e:
                    if attempt == 3: return e

        workers = max(1, min(getattr(cli, "dl_workers", 5), total))
        done = 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_fetch, i): i for i in range(total)}
            for fu in as_completed(futs):
                res = fu.result()
                if res is not True:
                    log(f"  antihook seg {futs[fu]}/{total} FAIL: {res}")
                    ex.shutdown(wait=False, cancel_futures=True)
                    shutil.rmtree(segdir, ignore_errors=True)
                    return None
                done += 1
                if progress: progress(done, total, 0, f"antihook {done}/{total} (x{workers})")
        tmp = out + ".ts.part"
        with open(tmp, "wb") as fo:
            for p in seg_paths:
                with open(p, "rb") as fi: fo.write(fi.read())
        shutil.rmtree(segdir, ignore_errors=True)
        rc = subprocess.call(["ffmpeg", "-y", "-loglevel", "error", "-i", tmp, "-c", "copy",
                              "-bsf:a", "aac_adtstoasc", out])
        if rc == 0 and os.path.exists(out) and os.path.getsize(out) > 1024:
            try: os.remove(tmp)
            except Exception: pass
            log(f"  antihook OK {os.path.getsize(out)//1024//1024}MB → {os.path.basename(out)}")
            return out
        ts_out = out[:-4] + ".ts" if out.endswith(".mp4") else out + ".ts"
        os.replace(tmp, ts_out)
        log(f"  antihook: ffmpeg fail → giữ {os.path.basename(ts_out)}")
        return ts_out
    except Exception as e:
        log(f"  antihook HLS lỗi: {e}"); return None


def download_native(cli, src, collection_id, episodes, outdir):
    code, j = cli.playlist(src, collection_id)
    data = j.get("data")
    eps = data if isinstance(data, list) else (data or {}).get("list", [])
    if not eps:
        return print("  playlist rỗng:", j.get("message") or j.get("_err") or "")
    want = parse_ranges(episodes, len(eps))
    os.makedirs(outdir, exist_ok=True)
    print(f"  {len(eps)} tập; tải {len(want)} tập (free+VIP) -> {outdir}")
    ok = 0
    for i in sorted(want):
        if download_episode(cli, src, eps[i - 1], outdir):
            ok += 1
    print(f"  === Xong: {ok}/{len(want)} tập ===")


def download_via_exe(exe, collection_id, episodes, outdir, cookie_file):
    cmd = [exe, "-cookie-file", cookie_file, "-collection-id", str(collection_id),
           "-download", "-episodes", episodes, "-output", outdir]
    print("  ->", " ".join(cmd))
    subprocess.call(cmd)


def parse_ranges(spec, n):
    if not spec:
        return list(range(1, n + 1))
    out = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-"); out.update(range(int(a), int(b) + 1))
        elif part:
            out.add(int(part))
    return {i for i in out if 1 <= i <= n}


def main():
    ap = argparse.ArgumentParser(description="MiniappVIP python client")
    ap.add_argument("cmd", choices=["check", "list", "browse", "playlist", "download"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--cookie", default=os.path.join(os.path.dirname(__file__), "cookie.local.txt"))
    ap.add_argument("--token", default="")
    ap.add_argument("--episodes", default="")
    ap.add_argument("--out", default="downloads")
    ap.add_argument("--via-exe", default="")
    ap.add_argument("--only", default="", help="lọc check theo status: builtin|custom")
    a = ap.parse_args()
    if not a.token:
        tf = os.path.join(os.path.dirname(__file__), "token.txt")
        if os.path.exists(tf):
            a.token = open(tf).read().strip()
    cli = Client(cookie=load_cookie(a.cookie), token=a.token)

    if a.cmd == "list":
        for s in SOURCES:
            print(f"  {s['status']:16} {s['adapter']:9} {s['app_id']:22} {s['name']}")
        return

    if a.cmd == "check":
        print(f"{'STATUS':16} {'ADAPTER':9} {'APP_ID':22} {'NAME':14} KẾT QUẢ")
        for s in SOURCES:
            if a.only and a.only not in s["status"]:
                continue
            ok, desc = cli.check(s)
            mark = "✅" if ok else "❌"
            print(f"{s['status']:16} {s['adapter']:9} {s['app_id']:22} {s['name']:14} {mark} {desc}")
        return

    if not a.args:
        return print("Thiếu tham số. Ví dụ: browse <nguồn>  |  playlist <nguồn> <id>")

    src = find_source(a.args[0])

    if a.cmd == "browse":
        code, j = cli.browse(src)
        for it in (j.get("data") or [])[:20]:
            print(f"  id={it.get('id')}  {it.get('name','?')[:40]}")
        if not j.get("data"):
            print("  (rỗng)", j.get("message") or j.get("_err") or "")
    elif a.cmd == "playlist":
        cid = a.args[1]
        code, j = cli.playlist(src, cid)
        eps = j.get("data") if isinstance(j.get("data"), list) else (j.get("data") or {}).get("list", [])
        for ep in (eps or [])[:80]:
            print(f"  seq={ep.get('sequence_no','?')} id={ep.get('id')} free={ep.get('is_free','?')} {ep.get('name','')[:30]}")
        if not eps:
            print("  (rỗng)", j.get("message") or j.get("_err") or "")
    elif a.cmd == "download":
        cid = a.args[1]
        if a.via_exe:
            download_via_exe(a.via_exe, cid, a.episodes or "1", a.out, a.cookie)
        else:
            download_native(cli, src, cid, a.episodes, a.out)


if __name__ == "__main__":
    main()
