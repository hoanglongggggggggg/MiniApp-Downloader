import frida, subprocess, time, os, sys, re
import requests, urllib3
urllib3.disable_warnings()
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
EXE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "extracted", "MiniappVIP-cli.exe")
COOKIE = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "cookie.local.txt")
PORT = 17693
PREFIXES = [b"duolai_", b"heiyan_", b"kuaishou_", b"fanxing_", b"xgfx_", b"koc_"]

if not os.path.exists(EXE):
    sys.exit(f"Không thấy exe: {EXE}")
if not os.path.exists(COOKIE):
    sys.exit(f"Không thấy cookie: {COOKIE}")

print(f"exe    : {EXE}\ncookie : {COOKIE}\nspawn server + discover để mint token...")
p = subprocess.Popen([EXE, "-no-open", "-port", str(PORT), "-cookie-file", COOKIE],
                     cwd=os.path.dirname(EXE), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
base = f"http://127.0.0.1:{PORT}"
for _ in range(30):
    try:
        requests.get(base + "/api/bootstrap", timeout=2); break
    except Exception:
        time.sleep(0.5)
try:
    requests.post(base + "/api/discover", json={}, timeout=50)
except Exception as e:
    print("discover:", e)
try:
    requests.post(base + "/api/playlist", json={"app_id": "tt5b4d2df0c47bdd2201",
                  "collection_id": "12270", "source_url": ""}, timeout=40)
except Exception:
    pass
time.sleep(0.5)

session = frida.attach(p.pid)
JS = r"""
rpc.exports = { scan(patt) {
  const out = [];
  for (const r of Process.enumerateRanges('rw-')) {
    try { for (const m of Memory.scanSync(r.base, r.size, patt)) {
      try {
        const b = new Uint8Array(m.address.readByteArray(48));
        let s=''; for (let i=0;i<b.length;i++){ s += (b[i]>=32&&b[i]<127)?String.fromCharCode(b[i]):' '; }
        const mm = s.match(/^[a-z]+_[0-9A-Fa-f]{32}/);
        if (mm && out.indexOf(mm[0]) < 0) out.push(mm[0]);
      } catch(e){}
    } } catch(e){}
  }
  return out;
} };
"""
script = session.create_script(JS)
script.load()

found = {}
for pref in PREFIXES:
    patt = " ".join("%02x" % b for b in pref)
    try:
        for tok in (script.exports_sync.scan(patt) or []):
            found[tok] = found.get(tok, 0) + 1
    except Exception:
        pass
try: p.kill()
except Exception: pass

print(f"\n=== TOKEN tìm được: {len(found)} ===")
for tok in sorted(found):
    print(f"  {tok}")

duolai = [t for t in found if t.startswith("duolai_")]
if duolai:
    open(os.path.join(HERE, "token.txt"), "w").write(duolai[0])
    print(f"\n-> ghi token duolai vào token.txt: {duolai[0]}")
    print("   Giờ chạy: python miniapp_client.py download 多来看点 <collection_id> --episodes 1")
else:
    print("\nKhông thấy token duolai_ — thử lại (mint có thể chậm) hoặc kiểm tra cookie còn hạn.")
