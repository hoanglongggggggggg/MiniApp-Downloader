import os
import json
import time

FIELDS = ("name", "cid", "app_id", "adapter", "project_id", "source_name",
          "cover_url", "eps", "note")


class Favorites:
    def __init__(self, path):
        self.path = path
        self.items = []
        try:
            if os.path.exists(path):
                self.items = json.load(open(path, encoding="utf-8")) or []
        except Exception:
            self.items = []

    def save(self):
        try:
            json.dump(self.items, open(self.path, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
        except Exception:
            pass

    @staticmethod
    def key(app_id, cid, project_id=None):
        return f"{app_id}:{cid}:{'' if project_id is None else project_id}"

    @classmethod
    def entry_from_src(cls, src, cid, name, cover_url=None, eps=None):
        return {
            "name": name or f"Bộ {cid}",
            "cid": str(cid),
            "app_id": src.get("app_id"),
            "adapter": src.get("adapter"),
            "project_id": src.get("project_id"),
            "source_name": src.get("name"),
            "cover_url": cover_url or "",
            "eps": eps,
        }

    def find(self, app_id, cid, project_id=None):
        k = self.key(app_id, str(cid), project_id)
        return next((x for x in self.items if x.get("key") == k), None)

    def is_fav(self, app_id, cid, project_id=None):
        return self.find(app_id, cid, project_id) is not None

    def src_of(self, entry):
        src = {"name": entry.get("source_name") or entry.get("app_id"),
               "app_id": entry.get("app_id"), "adapter": entry.get("adapter"),
               "status": "custom"}
        if entry.get("project_id") is not None:
            src["project_id"] = entry["project_id"]
        return src

    def add(self, entry, downloaded=None):
        k = self.key(entry.get("app_id"), str(entry.get("cid")), entry.get("project_id"))
        ex = next((x for x in self.items if x.get("key") == k), None)
        if ex:
            for f in FIELDS:
                if entry.get(f) not in (None, ""):
                    ex[f] = entry[f]
            if downloaded:
                ex["downloaded"] = True
            self.save()
            return ex
        e = {"key": k, "added": int(time.time()), "downloaded": bool(downloaded)}
        for f in FIELDS:
            e[f] = entry.get(f)
        e["cid"] = str(e["cid"])
        self.items.insert(0, e)
        self.save()
        return e

    def remove(self, app_id, cid, project_id=None):
        k = self.key(app_id, str(cid), project_id)
        self.items = [x for x in self.items if x.get("key") != k]
        self.save()

    def toggle(self, entry, downloaded=None):
        if self.is_fav(entry.get("app_id"), entry.get("cid"), entry.get("project_id")):
            self.remove(entry.get("app_id"), entry.get("cid"), entry.get("project_id"))
            return False
        self.add(entry, downloaded=downloaded)
        return True

    def all(self):
        return list(self.items)

    def __len__(self):
        return len(self.items)
