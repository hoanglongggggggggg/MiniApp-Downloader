import os
import sys
import json
import sqlite3
import tempfile
import shutil
import subprocess

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))

PACKAGES = [
    "com.ss.android.ugc.aweme",
    "com.ss.android.ugc.aweme.lite",
    "com.zhiliaoapp.musically",
]
WANT_APPID = sys.argv[1].strip() if len(sys.argv) > 1 else None


def run_adb(args):
    if isinstance(args, str):
        cmd = ["adb"] + args.split()
    else:
        cmd = ["adb"] + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.stdout.strip(), r.stderr.strip()


def adb_ready():
    out, _ = run_adb("devices")
    lines = [l for l in out.splitlines()[1:] if l.strip() and "\tdevice" in l]
    return bool(lines)


def pull_storage_dbs(tmp_dir):
    found = []
    staging = "/sdcard/adb_token_tmp"
    for pkg in PACKAGES:
        run_adb(["shell", "su", "-c", f"mkdir -p {staging}"])
        run_adb(["shell", "su", "-c",
                 f"cp /data/data/{pkg}/databases/tma_storage* {staging}/ 2>/dev/null; "
                 f"cp /data/data/{pkg}/databases/appbrand_storage* {staging}/ 2>/dev/null"])
        run_adb(["pull", staging + "/.", tmp_dir])
        run_adb(["shell", "su", "-c", f"rm -rf {staging}"])
    for f in os.listdir(tmp_dir):
        if f.endswith(".db"):
            found.append(os.path.join(tmp_dir, f))
    return found


def scan_db(db_path):
    hits = {}
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [t[0] for t in cur.fetchall()]
        for tb in tables:
            try:
                cur.execute(f"SELECT app_id, key, data FROM {tb}")
            except Exception:
                continue
            for app_id, k, v in cur.fetchall():
                if not app_id or v is None:
                    continue
                if WANT_APPID and app_id != WANT_APPID:
                    continue
                kl = str(k).lower()
                rec = hits.setdefault(app_id, {"app_id": app_id, "token": "", "deviceid": ""})
                if "token" in kl:
                    rec["token"] = str(v)
                elif "deviceid" in kl or "device_id" in kl:
                    rec["deviceid"] = str(v)
        conn.close()
    except Exception:
        pass
    return [r for r in hits.values() if r["token"] or r["deviceid"]]


def main():
    print("[*] Kiểm tra ADB...")
    if not adb_ready():
        sys.exit("[-] Không thấy thiết bị ADB (cắm máy + bật USB debugging + `adb devices`).")

    tmp_dir = tempfile.mkdtemp(prefix="adb_token_")
    try:
        print("[*] Kéo storage mini-app (cần root/su)...")
        dbs = pull_storage_dbs(tmp_dir)
        if not dbs:
            sys.exit("[-] Không kéo được file storage nào (máy chưa root? chưa mở mini-app lần nào?).")

        all_hits = {}
        for db in dbs:
            for rec in scan_db(db):
                a = rec["app_id"]
                cur = all_hits.setdefault(a, {"app_id": a, "token": "", "deviceid": ""})
                cur["token"] = rec["token"] or cur["token"]
                cur["deviceid"] = rec["deviceid"] or cur["deviceid"]

        withtok = [r for r in all_hits.values() if r["token"]]
        print(f"\n=== app_id có token: {len(withtok)} / tổng {len(all_hits)} app_id thấy trong storage ===")
        for r in all_hits.values():
            mark = "TOKEN" if r["token"] else "     "
            print(f"  [{mark}] {r['app_id']}  token={r['token'][:24]}{'…' if len(r['token'])>24 else ''}  device={r['deviceid'][:16]}")

        if not withtok:
            sys.exit("\n[-] Thấy storage nhưng KHÔNG có token (chưa đăng nhập mini-app? mở app + login rồi chạy lại).")

        pick = withtok[0]
        if WANT_APPID:
            match = [r for r in withtok if r["app_id"] == WANT_APPID]
            if match:
                pick = match[0]

        open(os.path.join(HERE, "token.txt"), "w", encoding="utf-8").write(pick["token"])
        print(f"\n-> token.txt  <- {pick['token']}  (app_id {pick['app_id']})")
        if pick["deviceid"]:
            open(os.path.join(HERE, "deviceid.local.txt"), "w", encoding="utf-8").write(pick["deviceid"])
            print(f"-> deviceid.local.txt <- {pick['deviceid']}  (ĐÈ deviceid cũ — buộc phải khớp để giải mã r_sign)")
        else:
            print("[!] Không thấy deviceid trong DB — giữ deviceid.local.txt cũ, r_sign CÓ THỂ giải sai.")
        print("\n   Thử ngay: python miniapp_client.py download 黑岩漫故事(HEIY) <collection_id> --episodes 1")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
