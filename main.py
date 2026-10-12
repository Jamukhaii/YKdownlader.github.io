import os
import re
import sys
import threading
import time
from pathlib import Path
from datetime import datetime
from flask import Flask, render_template, request, jsonify

try:
    import yt_dlp
except ImportError:
    print("HATA: yt-dlp bulunamadı!")
    print("Lütfen şu komutu çalıştırın: pip install -U yt-dlp")
    sys.exit(1)

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False

BASE_DIR = Path(__file__).parent
DOWNLOAD_DIR = BASE_DIR / "downloads"
DOWNLOAD_DIR.mkdir(exist_ok=True)

state = {
    "running": False,
    "queue_total": 0,
    "queue_done": 0,
    "current_url": "",
    "current_title": "",
    "progress": 0,
    "speed": "N/A",
    "eta": "N/A",
    "status_text": "Beklemede...",
    "logs": [],
    "errors": []
}

lock = threading.Lock()

def log_msg(msg, mtype="info"):
    with lock:
        ts = datetime.now().strftime("%H:%M:%S")
        state["logs"].append({"time": ts, "text": str(msg), "type": mtype})
        if len(state["logs"]) > 70:
            state["logs"] = state["logs"][-55:]

def reset_state():
    with lock:
        state.update({
            "running": False,
            "queue_total": 0,
            "queue_done": 0,
            "current_url": "",
            "current_title": "",
            "progress": 0,
            "speed": "N/A",
            "eta": "N/A",
            "status_text": "Beklemede...",
            "logs": [],
            "errors": []
        })

def hook(d):
    with lock:
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            down = d.get("downloaded_bytes", 0)
            if total > 0:
                state["progress"] = int((down / total) * 100)
            sp = d.get("speed") or 0
            if sp > 0:
                if sp >= 1024**2:
                    state["speed"] = f"{sp/1024**2:.1f} MB/s"
                elif sp >= 1024:
                    state["speed"] = f"{sp/1024:.1f} KB/s"
                else:
                    state["speed"] = f"{sp:.0f} B/s"
            else:
                state["speed"] = "N/A"
            et = d.get("eta")
            if et and et > 0:
                sec = int(et)
                m, s = divmod(sec, 60)
                h, m2 = divmod(m, 60)
                if h:
                    state["eta"] = f"{h:02d}:{m2:02d}:{s:02d}"
                else:
                    state["eta"] = f"{m2:02d}:{s:02d}"
            else:
                state["eta"] = "N/A"
            state["status_text"] = f"İndiriliyor... %{state['progress']}"
        elif d.get("status") == "finished":
            state["progress"] = 100
            state["status_text"] = "Birleştiriliyor / Dönüştürülüyor..."
            state["speed"] = "N/A"
            state["eta"] = "N/A"
        elif d.get("status") == "error":
            state["status_text"] = "İndirme sırasında hata oluştu"

def build_opts(fmt, height):
    outtmpl = str(DOWNLOAD_DIR / "%(title).150B.%(ext)s")
    opts = {
        "outtmpl": outtmpl,
        "noplaylist": False,
        "ignoreerrors": True,
        "retries": 14,
        "fragment_retries": 14,
        "concurrent_fragment_downloads": 4,
        "socket_timeout": 60,
        "timeout": 1800,
        "extractor_retries": 4,
        "geo_bypass": True,
        "nocheckcertificate": False,
        "windowsfilenames": True,
        "restrictfilenames": True,
        "trim_file_name": 150,
        "progress_hooks": [hook],
        "quiet": True,
        "no_warnings": True,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7"
        }
    }
    if fmt == "mp3":
        opts.update({
            "format": "bestaudio/best",
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"},
                {"key": "FFmpegMetadata"}
            ],
            "keepvideo": False
        })
        return opts
    if height is None:
        fsel = "bestvideo*+bestaudio/bestvideo+bestaudio/best"
    else:
        fsel = f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/bestvideo[height<={height}]+bestaudio/best"
    opts.update({"format": fsel, "merge_output_format": "mp4", "prefer_ffmpeg": True})
    return opts

def worker(urls, fmt, height):
    reset_state()
    with lock:
        state["running"] = True
        state["queue_total"] = len(urls)
        state["queue_done"] = 0
        state["status_text"] = "Kuyruk başlatıldı..."
        state["errors"] = []
    log_msg(f"Toplam {len(urls)} link | Format: {fmt.upper()} | Kalite: {'AUTO' if fmt=='mp4' and height is None else ('MP3' if fmt=='mp3' else f'{height}p')}")
    for i, url in enumerate(urls, 1):
        u = url.strip()
        with lock:
            state["current_url"] = u
            state["current_title"] = ""
            state["progress"] = 0
            state["speed"] = "N/A"
            state["eta"] = "N/A"
            state["status_text"] = f"[{i}/{state['queue_total']}] Hazırlanıyor..."
        log_msg(f"[{i}/{state['queue_total']}] İşleniyor: {u}")
        try:
            with yt_dlp.YoutubeDL(build_opts(fmt, height)) as ydl:
                try:
                    info = ydl.extract_info(u, download=False)
                    if info:
                        with lock:
                            state["current_title"] = info.get("title") or u
                        log_msg(f"Başlık: {state['current_title']}")
                except Exception:
                    pass
                ydl.download([u])
            with lock:
                state["queue_done"] += 1
            log_msg("Başarılı ✓", "ok")
        except Exception as e:
            with lock:
                state["errors"].append(f"{u} | {e}")
                state["queue_done"] += 1
            log_msg("Başarısız ✗", "err")
        time.sleep(1)
    with lock:
        state["running"] = False
        state["current_url"] = ""
        state["current_title"] = ""
        state["progress"] = 0
        state["speed"] = "N/A"
        state["eta"] = "N/A"
        state["status_text"] = f"Tamamlandı | {state['queue_done']}/{state['queue_total']} | Hata: {len(state['errors'])}"
    log_msg("Kuyruk tamamlandı.")

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/start", methods=["POST"])
def start():
    if state["running"]:
        return jsonify({"ok": False, "msg": "Zaten indirme devam ediyor."}), 400
    data = request.get_json(silent=True) or {}
    raw = (data.get("links") or "").strip()
    fmt = (data.get("format") or "mp4").lower()
    qv = (data.get("quality") or "auto")
    if not raw:
        return jsonify({"ok": False, "msg": "Lütfen en az 1 link girin."}), 400
    urls = []
    for line in raw.replace("\r", "\n").split("\n"):
        line = line.strip()
        if not line:
            continue
        if re.match(r"^https?://\S+", line):
            urls.append(line)
    if not urls:
        return jsonify({"ok": False, "msg": "Geçerli HTTP/HTTPS linki bulunamadı."}), 400
    height = None
    if fmt == "mp4":
        m = {"1080":1080,"720":720,"480":480,"360":360,"240":240}
        height = m.get(qv)
    if fmt not in ("mp3","mp4"):
        fmt = "mp4"
    t = threading.Thread(target=worker, args=(urls, fmt, height), daemon=True)
    t.start()
    return jsonify({"ok": True, "msg": f"{len(urls)} link kuyruğa eklendi."})

@app.route("/status")
def status():
    with lock:
        return jsonify(dict(state))

if __name__ == "__main__":
    print("YK DOWNLOADER | http://127.0.0.1:5000")
    print("Hedef:", DOWNLOAD_DIR)
    app.run(host="127.0.0.1", port=5000, debug=False)
