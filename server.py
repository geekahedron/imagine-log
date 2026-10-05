"""Local catalog server. Stdlib only, plus ffmpeg if it is on PATH."""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading

ROOT = os.path.dirname(os.path.abspath(__file__))
MEDIA = os.path.join(ROOT, "media")
THUMBS = os.path.join(ROOT, "thumbs")
EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".webm", ".mov"}
VIDS = {".mp4", ".webm", ".mov"}


def ensure_midframe(name):
    ext = os.path.splitext(name)[1].lower()
    if ext not in VIDS:
        return None
    os.makedirs(THUMBS, exist_ok=True)
    out = os.path.join(THUMBS, os.path.splitext(name)[0] + ".jpg")
    if os.path.isfile(out) and os.path.getsize(out) > 0:
        return "thumbs/" + os.path.basename(out)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    src = os.path.join(MEDIA, name)
    try:
        probe = subprocess.run(
            [ffmpeg, "-i", src],
            capture_output=True, text=True, timeout=30,
        )
        dur = 1.0
        for line in (probe.stderr or "").splitlines():
            if "Duration:" in line:
                hms = line.split("Duration:", 1)[1].split(",")[0].strip()
                h, m, s = hms.split(":")
                dur = int(h) * 3600 + int(m) * 60 + float(s)
                break
        mid = max(0.1, dur / 2)
        subprocess.run(
            [ffmpeg, "-y", "-ss", str(mid), "-i", src, "-frames:v", "1", "-q:v", "3", out],
            capture_output=True, timeout=60, check=False,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    if os.path.isfile(out) and os.path.getsize(out) > 0:
        return "thumbs/" + os.path.basename(out)
    return None


def gray32(path, ss=None):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    cmd = [ffmpeg, "-v", "error"]
    if ss is not None:
        cmd += ["-ss", str(ss)]
    cmd += ["-i", path, "-frames:v", "1", "-vf", "scale=32:32", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if len(out.stdout) < 1024:
        return None
    return out.stdout[:1024]


def diff(a, b):
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def match_clips():
    stills = []
    videos = []
    for n in os.listdir(MEDIA):
        ext = os.path.splitext(n)[1].lower()
        if ext in VIDS:
            videos.append(n)
        elif ext in EXTS:
            stills.append(n)
    prints = []
    for n in stills:
        g = gray32(os.path.join(MEDIA, n))
        if g:
            prints.append((os.path.splitext(n)[0], g))
    found = []
    for n in videos:
        g = gray32(os.path.join(MEDIA, n), ss=0)
        if not g or not prints:
            continue
        scored = sorted((diff(g, p[1]), p[0]) for p in prints)
        best, second = scored[0], scored[1] if len(scored) > 1 else (999, None)
        if best[0] < 18 and (second[0] - best[0]) > 4:
            found.append({"video": os.path.splitext(n)[0], "still": best[1], "score": round(best[0], 2)})
    return found


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/media":
            items = []
            missing = False
            if os.path.isdir(MEDIA):
                for n in os.listdir(MEDIA):
                    if os.path.splitext(n)[1].lower() not in EXTS:
                        continue
                    st = os.stat(os.path.join(MEDIA, n))
                    thumb = None
                    stem, ext = os.path.splitext(n)
                    if ext.lower() in VIDS:
                        ready = os.path.join(THUMBS, stem + ".jpg")
                        if os.path.isfile(ready) and os.path.getsize(ready) > 0:
                            thumb = "thumbs/" + stem + ".jpg"
                        else:
                            missing = True
                    cache = {}
                    cache_path = os.path.join(THUMBS, "durations.json")
                    if os.path.isfile(cache_path):
                        try:
                            with open(cache_path, encoding="utf-8") as f:
                                cache = json.load(f)
                        except (OSError, json.JSONDecodeError):
                            cache = {}
                    items.append({
                        "name": n,
                        "mtime": int(st.st_mtime),
                        "thumb": thumb,
                        "duration": cache.get(n),
                    })
                items.sort(key=lambda x: x["name"])
            if missing:
                start_thumbs()
            self._json(items)
            return
        if path == "/api/thumbs":
            made = 0
            if os.path.isdir(MEDIA):
                for n in os.listdir(MEDIA):
                    if ensure_midframe(n):
                        made += 1
            self._json({"ok": True, "thumbs": made})
            return
        if path == "/api/imports":
            found = []
            files = []
            errors = []
            imports = os.path.join(ROOT, "imports")
            done = os.path.join(imports, "processed")
            os.makedirs(done, exist_ok=True)
            for name in sorted(os.listdir(imports)):
                src = os.path.join(imports, name)
                if not os.path.isfile(src) or not name.startswith("catalog") or not name.endswith(".txt"):
                    continue
                files.append(name)
                with open(src, encoding="utf-8") as f:
                    text = f.read()
                text = text.split("=", 1)[-1].strip().rstrip(";")
                try:
                    rows = json.loads(text)
                except json.JSONDecodeError as err:
                    errors.append(name + ": " + str(err))
                    continue
                if isinstance(rows, list):
                    found.extend(rows)
                target = os.path.join(done, name)
                if os.path.exists(target):
                    target = os.path.join(done, name.replace(".txt", "-" + str(int(os.path.getmtime(src))) + ".txt"))
                os.replace(src, target)
            self._json({"files": files, "records": found, "errors": errors})
            return
        if path == "/api/styles":
            styles = []
            ini = os.path.join(ROOT, "styles.ini")
            if os.path.isfile(ini):
                with open(ini, encoding="utf-8") as f:
                    styles = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
            self._json(styles)
            return
        return super().do_GET()

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/match":
            self._json(match_clips())
            return
        if path == "/api/delete":
            length = int(self.headers.get("Content-Length", "0"))
            names = json.loads(self.rfile.read(length).decode("utf-8"))
            removed = []
            for name in names:
                base = os.path.basename(name)
                src = os.path.join(MEDIA, base)
                if os.path.isfile(src):
                    os.remove(src)
                    removed.append(base)
                stem = os.path.splitext(base)[0]
                for ext in (".jpg", ".png", ".webp"):
                    thumb = os.path.join(THUMBS, stem + ext)
                    if os.path.isfile(thumb):
                        os.remove(thumb)
            self._json({"ok": True, "removed": removed})
            return
        if path != "/api/catalog":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        data = json.loads(raw.decode("utf-8"))
        body = "window.CATALOG = " + json.dumps(data, indent=2) + ";\n"
        with open(os.path.join(ROOT, "catalog.js"), "w", encoding="utf-8") as f:
            f.write(body)
        self._json({"ok": True, "count": len(data)})

    def _json(self, obj):
        raw = json.dumps(obj).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def duration_of(name):
    ext = os.path.splitext(name)[1].lower()
    if ext not in VIDS:
        return None
    cache_path = os.path.join(THUMBS, "durations.json")
    cache = {}
    if os.path.isfile(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as f:
                cache = json.load(f)
        except (OSError, json.JSONDecodeError):
            cache = {}
    if name in cache:
        return cache[name]
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    src = os.path.join(MEDIA, name)
    try:
        probe = subprocess.run([ffmpeg, "-i", src], capture_output=True, text=True, timeout=20)
        dur = None
        for line in (probe.stderr or "").splitlines():
            if "Duration:" in line:
                hms = line.split("Duration:", 1)[1].split(",")[0].strip()
                h, m, s = hms.split(":")
                dur = int(h) * 3600 + int(m) * 60 + float(s)
                break
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    if dur is None:
        return None
    cache[name] = round(dur, 2)
    os.makedirs(THUMBS, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache, f)
    return cache[name]


def start_thumbs():
    if getattr(start_thumbs, "running", False):
        return
    start_thumbs.running = True
    def run():
        try:
            build_missing_thumbs()
        finally:
            start_thumbs.running = False
    threading.Thread(target=run, daemon=True).start()


def build_missing_thumbs():
    if not os.path.isdir(MEDIA):
        return
    for n in os.listdir(MEDIA):
        try:
            duration_of(n)
            ensure_midframe(n)
        except Exception:
            continue


def file_hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_catalog():
    path = os.path.join(ROOT, "catalog.js")
    if not os.path.isfile(path):
        return []
    text = open(path, encoding="utf-8").read().split("=", 1)[-1].strip().rstrip(";")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def write_catalog(data):
    body = "window.CATALOG = " + json.dumps(data, indent=2) + ";\n"
    with open(os.path.join(ROOT, "catalog.js"), "w", encoding="utf-8") as f:
        f.write(body)


def merge_rec(base, extra):
    if not base.get("prompt") and extra.get("prompt"):
        base["prompt"] = extra["prompt"]
    if not base.get("style") and extra.get("style"):
        base["style"] = extra["style"]
    if not base.get("notes") and extra.get("notes"):
        base["notes"] = extra["notes"]
    elif extra.get("notes") and extra.get("notes") != base.get("notes"):
        base["notes"] = (base.get("notes") or "") + "\n" + extra["notes"]
    if not base.get("parent") and extra.get("parent"):
        base["parent"] = extra["parent"]
    if not base.get("label") and extra.get("label"):
        base["label"] = extra["label"]
    base["stars"] = max(int(base.get("stars") or 0), int(extra.get("stars") or 0))
    base["nsfw"] = bool(base.get("nsfw") or extra.get("nsfw"))
    tags = list(base.get("tags") or [])
    have = {t.lower() for t in tags}
    for tag in extra.get("tags") or []:
        if tag.lower() not in have:
            tags.append(tag)
    base["tags"] = tags
    conv = list(base.get("conversation") or [])
    if isinstance(conv, str):
        conv = [conv] if conv else []
    more = extra.get("conversation") or []
    if isinstance(more, str):
        more = [more] if more else []
    for item in more:
        if item and item not in conv:
            conv.append(item)
    base["conversation"] = conv
    return base


def clean_duplicate_downloads():
    if not os.path.isdir(MEDIA):
        return
    names = [n for n in os.listdir(MEDIA) if os.path.splitext(n)[1].lower() in EXTS]
    catalog = load_catalog()
    by_stem = {}
    for rec in catalog:
        file_name = os.path.basename(rec.get("file") or "")
        by_stem[os.path.splitext(file_name)[0]] = rec
        by_stem.setdefault(rec.get("id") or "", rec)
    removed = 0
    for name in names:
        stem, ext = os.path.splitext(name)
        match = re.fullmatch(r"(.+) \((\d+)\)", stem)
        if not match:
            continue
        base_name = match.group(1) + ext
        if base_name not in names:
            continue
        extra_path = os.path.join(MEDIA, name)
        base_path = os.path.join(MEDIA, base_name)
        try:
            same = file_hash(extra_path) == file_hash(base_path)
        except OSError:
            continue
        if not same:
            print("Left different download", name)
            continue
        base = by_stem.get(os.path.splitext(base_name)[0])
        extra = by_stem.get(stem)
        if base is not None and extra is not None and base is not extra:
            merge_rec(base, extra)
            catalog = [rec for rec in catalog if rec is not extra]
        os.remove(extra_path)
        thumb = os.path.join(THUMBS, stem + ".jpg")
        if os.path.isfile(thumb):
            os.remove(thumb)
        removed += 1
        print("Removed duplicate", name)
    if removed:
        write_catalog(catalog)
        print("Removed", removed, "duplicate downloads")


if __name__ == "__main__":
    os.chdir(ROOT)
    clean_duplicate_downloads()
    threading.Thread(target=start_thumbs, daemon=True).start()
    print("Imagine log at http://127.0.0.1:8765/")
    ThreadingHTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
