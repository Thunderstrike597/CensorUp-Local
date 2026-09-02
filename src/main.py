# --- Suppress console windows from ffmpeg/ffprobe (and any other child
# process, e.g. the ones openai-whisper/yt-dlp launch internally) on Windows.
# Must run before censor_script / url_downloader_script are imported below,
# since those are what eventually trigger the subprocess calls.
import sys as _sys
if _sys.platform == "win32":
    import subprocess as _subprocess
    _original_popen_init = _subprocess.Popen.__init__

    def _no_window_popen_init(self, *args, **kwargs):
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | _subprocess.CREATE_NO_WINDOW
        return _original_popen_init(self, *args, **kwargs)

    _subprocess.Popen.__init__ = _no_window_popen_init

from fasthtml.common import *
from fasthtml.components import *
from censor_script import censor_media
from url_downloader_script import download_video
# --- Logging to a file (windowed mode has no console to print to, and
# print()/exceptions were previously being discarded into os.devnull -
# see below. Everything now goes to logs\censorup.log next to the exe
# instead, so a crash can actually be diagnosed after the fact.) ---
import os, logging
from logging.handlers import RotatingFileHandler


def _log_file_path():
    if getattr(_sys, 'frozen', False):
        log_dir = os.path.join(os.path.dirname(_sys.executable), "logs")
    else:
        log_dir = os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, "censorup.log")


LOG_PATH = _log_file_path()

_log_handler = RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
_log_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))

_root_logger = logging.getLogger()
_root_logger.setLevel(logging.INFO)
_root_logger.addHandler(_log_handler)


class _StreamToLogger:
    """A drop-in stand-in for sys.stdout/sys.stderr that feeds whatever
    gets written to it (e.g. any print() call, anywhere in the app or a
    library) into the logging system above, line by line, instead of a
    real console/file. Used only in --windowed frozen mode, where there's
    no real console to write to."""
    def __init__(self, level):
        self.level = level
        self._buffer = ""

    def write(self, message):
        self._buffer += message
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line.strip():
                logging.log(self.level, line)

    def flush(self):
        pass

    def isatty(self):
        return False


import os, threading, time, json, uuid, sys, io
from typing import Optional
import base64
from urllib.parse import urlparse
import urllib.request
import webbrowser
import socket
from starlette.responses import FileResponse, StreamingResponse
from starlette.requests import Request
import asyncio

import pystray
from PIL import Image, ImageDraw

# Fix for PyInstaller --windowed mode (no console)
# Prevents 'NoneType' object has no attribute 'write' from print/tqdm.
# Previously sent everything to os.devnull, silently discarding every
# print() AND every unhandled exception traceback - now goes to the log
# file above instead, so crashes can actually be diagnosed.
if getattr(sys, 'frozen', False):
    sys.stdout = _StreamToLogger(logging.INFO)
    sys.stderr = _StreamToLogger(logging.ERROR)
    logging.info("=== CensorUp-Local starting (frozen build) ===")

# Initialize FastHTML App
app = FastHTML()
rt = app.route
os.makedirs("uploads", exist_ok=True)

# --- Tray icon + auto-close when no browser tab/window is open ---
PORT = 5001
CONNECTION_GRACE = 8  # seconds with zero open /watch connections before
# shutting down. This absorbs a brief gap during a page refresh (the old
# connection drops and a new one opens a moment later) without mistaking
# it for a real close.
DEBUG = True          # prints live connection-count info from the watchdog

_active_connections = set()
_connections_lock = threading.Lock()
_tray_icon = {"icon": None}

# --- Background job tracking (for the live progress bar) ---
JOBS = {}
JOBS_LOCK = threading.Lock()

@rt('/favicon.ico')
def get():
    if getattr(sys, 'frozen', False):
        icon_path = os.path.join(sys._MEIPASS, "assets", "icon.ico")
    else:
        icon_path = os.path.join(os.path.dirname(__file__), "assets", "icon.ico")

    if os.path.isfile(icon_path):
        return FileResponse(icon_path, media_type="image/x-icon")
    return Response(status_code=404)

def _icon_ico_path():
    if getattr(sys, 'frozen', False):
        return os.path.join(sys._MEIPASS, "assets", "icon.ico")
    return os.path.join(os.path.dirname(__file__), "assets", "icon.ico")

@rt('/icon-{size}.png')
def get(size: int):
    """Generates a PNG at the requested size from the bundled .ico on the
    fly - manifest.json below points at this instead of needing separate
    pre-exported PNG files. Edge's PWA-install feature (the source of the
    taskbar/title-bar icon) reads icons from the manifest, not favicon.ico."""
    ico_path = _icon_ico_path()
    if not os.path.isfile(ico_path):
        return Response(status_code=404)
    img = Image.open(ico_path)
    # .ico files bundle multiple resolutions; Pillow picks the closest one
    # available and we resize from there for a clean result at any size.
    img = img.convert("RGBA").resize((size, size), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return Response(content=buf.getvalue(), media_type="image/png")

@rt('/manifest.json')
def get():
    manifest = {
        "name": "CensorUp-Local",
        "short_name": "CensorUp-Local",
        "start_url": "/",
        "display": "standalone",
        "icons": [
            {"src": "/icon-192.png?v=2", "sizes": "192x192", "type": "image/png"},
            {"src": "/icon-512.png?v=2", "sizes": "512x512", "type": "image/png"},
        ],
    }
    return Response(content=json.dumps(manifest), media_type="application/manifest+json")

@rt('/watch')
async def get(request: Request):
    """A long-lived connection the page keeps open for as long as it's
    running. Unlike a JS setInterval heartbeat, this stays open at the
    browser's network layer even if the tab's JavaScript gets fully
    suspended while backgrounded for a long time — so the watchdog below
    can trust it even through aggressive tab-sleeping behavior."""
    conn_id = uuid.uuid4().hex

    async def event_stream():
        with _connections_lock:
            _active_connections.add(conn_id)
        try:
            while True:
                if await request.is_disconnected():
                    break
                yield "data: ping\n\n"
                await asyncio.sleep(5)
        finally:
            with _connections_lock:
                _active_connections.discard(conn_id)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _shutdown():
    print("🛑 No open browser window detected - shutting down.")
    icon = _tray_icon.get("icon")
    if icon is not None:
        try:
            icon.stop()
        except Exception:
            pass
    time.sleep(0.2)
    os._exit(0)


def watch_for_page_close():
    zero_since = None
    while True:
        try:
            time.sleep(1)
            with _connections_lock:
                count = len(_active_connections)
            if count > 0:
                zero_since = None
                if DEBUG:
                    print(f"[watchdog] {count} open connection(s)")
                continue
            if zero_since is None:
                zero_since = time.time()
            elapsed = time.time() - zero_since
            if DEBUG:
                print(f"[watchdog] no open connections for {elapsed:.1f}s (shuts down at {CONNECTION_GRACE}s)")
            if elapsed > CONNECTION_GRACE:
                _shutdown()
                return
        except Exception as e:
            if DEBUG:
                print(f"[watchdog] error in watchdog loop: {e}")


def _make_tray_image():
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((8, 8, 56, 56), fill=(64, 156, 255, 255))
    return img


def _open_from_tray(icon, item):
    edge_cmd = f'start msedge --app="http://127.0.0.1:{PORT}"'
    try:
        os.system(edge_cmd)
    except Exception:
        webbrowser.open(f"http://127.0.0.1:{PORT}")


def _quit_from_tray(icon, item):
    _shutdown()


def run_tray_once_ready():
    for _ in range(30):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}", timeout=1)
            break
        except Exception:
            time.sleep(1)

    icon = pystray.Icon(
        "censorup",
        _make_tray_image(),
        "CensorUp",
        menu=pystray.Menu(
            pystray.MenuItem("Open CensorUp", _open_from_tray, default=True),
            pystray.MenuItem("Quit", _quit_from_tray),
        ),
    )
    _tray_icon["icon"] = icon
    icon.run()


def resource_path(relative_path):
    """Resolve a path to a bundled resource (works both running from source
    and running as a frozen PyInstaller build, onedir or onefile)."""
    if getattr(sys, 'frozen', False):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(__file__)
    return os.path.join(base, relative_path)


def load_defaults():
    try:
        with open("defaults.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"default_sound_effect": "", "default_censor_words": "", "default_start_fraction": "0.225", "default_end_fraction": "0.12"}

def fraction_to_percent_str(fraction, fallback=0.0):
    """Mirrors the JS formula in the slider's oninput handler
    (Math.round(this.value*1000)/10 + '%'), so the label shown on page
    load matches what the slider would show if you dragged it there.
    Falls back to `fallback` if the value is missing/invalid (e.g. an
    older defaults.json that predates these keys), instead of crashing
    the whole homepage."""
    try:
        pct = round(float(fraction) * 1000) / 10
    except (TypeError, ValueError):
        pct = round(float(fallback) * 1000) / 10
    if pct == int(pct):          # e.g. 12.0 -> "12%" instead of "12.0%"
        return f"{int(pct)}%"
    return f"{pct}%"

DEFAULTS = load_defaults()


def delete_old_local_files():
    while True:
        now = time.time()
        for fname in os.listdir("uploads"):
            fpath = os.path.join("uploads", fname)
            try:
                if os.path.isfile(fpath) and now - os.path.getmtime(fpath) > 3600:
                    os.remove(fpath)
                    print(f"🧹 Deleted old file: {fname}")
            except OSError:
                pass
        time.sleep(3650)


Defaults = (
    # DaisyUI 5 + Tailwind CSS browser
    Link(rel="stylesheet", href="https://cdn.jsdelivr.net/npm/daisyui@5", type="text/css"),
    Link(rel="stylesheet", href="https://cdn.jsdelivr.net/npm/daisyui@5/themes.css", type="text/css"),
    # ICON_VERSION busts Edge/Chrome's aggressive favicon caching - it's
    # keyed by history, not the normal HTTP cache, so a plain refresh
    # doesn't pick up a changed/newly-added icon. Bump this string any
    # time the icon file itself changes, so returning visitors (including
    # anyone who saw an earlier broken build) get a genuinely new URL the
    # browser has never cached anything for.
    Link(rel="icon", href="/favicon.ico?v=2", type="image/x-icon"),
    Link(rel="shortcut icon", href="/favicon.ico?v=2", type="image/x-icon"),
    Link(rel="apple-touch-icon", href="/favicon.ico?v=2"),
    Link(rel="manifest", href="/manifest.json?v=2"),
    
    Script(src="https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4"),

    # Tabler Icons (for the GitHub icon etc.)
    Link(rel="stylesheet", href="https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@latest/tabler-icons.min.css"),

    # Optional analytics
    Script(src="https://umami.is", data_website_id="1cdc08e2-77c1-4c03-b680-9e7640e41616"),

    Title("CensorUp — Automatic Profanity Censorship, No Signup Required"),
    Meta(name="viewport", content="width=device-width, initial-scale=1"),
    Meta(name="description", content="CensorUp automatically censors profanity and unwanted words from audio and video."),

    # Connection-watchdog script — opens a persistent connection that the
    # server uses to know the window is still open (see /watch route).
    # EventSource has built-in auto-reconnect, and the onerror handler below
    # gives it a nudge too, so brief hiccups (e.g. the server itself briefly
    # busy) don't get mistaken for the window closing.
    Script(NotStr("""
        (function() {
            function connect() {
                const es = new EventSource('/watch');
                es.onerror = function() {
                    es.close();
                    setTimeout(connect, 1000);
                };
            }
            connect();
        })();
    """)),
)


@rt('/')
def get(sess):
    try:
        return _render_homepage(sess)
    except Exception:
        logging.exception("Homepage ('/') route crashed")
        return Div(
            Div("⚠️ Something went wrong loading the page. Check logs\\censorup.log for details.", cls="alert alert-error"),
        )


def _render_homepage(sess):
    navbar = Div(
        Div("🤫 CensorUp", cls="text-2xl text-info font-bold navbar-start"),
        Div(
            A(I(cls="ti ti-brand-github-filled text-lg"), " Github", href="https://github.com", cls="btn"),
            cls="text-warning font-bold navbar-end space-x-5 drop-shadow-[0_1.2px_1.2px_rgba(0,0,0,0.8)]"
        ),
        cls="navbar bg-ghost py-2 fixed z-50"
    )

    default_start_fraction = DEFAULTS.get("default_start_fraction") or 0.225
    default_end_fraction = DEFAULTS.get("default_end_fraction") or 0.12

    first_hero = Div(
        Div(
            Div(
                Div("🎬 Upload your Audio/Video File to Censor it Automatically!", cls="text-2xl font-bold text-netural mb-4"),
                Form(
                    Div(
                        Input(type="file", name="beep_file", cls="file-input file-input-sm", accept="audio/*"),
                        Div("🔊 Optional: sound effect to play instead of silence", cls="text-xs opacity-70 ml-2 self-center"),
                        cls="flex w-full flex-col lg:flex-row items-start lg:items-center mb-3"
                    ),
                    Div(
                        Input(type="file", name="file", id="video-file-input", cls="file-input"),
                        Div("OR", cls="divider lg:divider-horizontal"),
                        Input(type="text", name="url", placeholder="🌐 Enter a YouTube / Facebook / Instagram / TikTok URL", cls="input input-bordered w-full"),
                        cls="flex w-full flex-col lg:flex-row"
                    ),
                    Div(
                        Div(
                            Label(
                                Span("🔈 Audible start of each word", cls="text-sm"),
                                Span(fraction_to_percent_str(default_start_fraction), id="keep-start-value", cls="text-sm font-mono opacity-70"),
                                cls="flex justify-between items-center"
                            ),
                            Input(
                                type="range", name="keep_start_fraction", id="keep-start-slider",
                                min="0", max="1", step="0.01", value=default_start_fraction,
                                cls="range range-info range-xs w-full",
                                oninput="document.getElementById('keep-start-value').textContent = Math.round(this.value*1000)/10 + '%'"
                            ),
                            Div("How much of the start of each censored word stays audible", cls="text-xs opacity-60"),
                            cls="mt-4"
                        ),
                        Div(
                            Label(
                                Span("🔈 Audible end of each word", cls="text-sm"),
                                Span(fraction_to_percent_str(default_end_fraction), id="keep-end-value", cls="text-sm font-mono opacity-70"),
                                cls="flex justify-between items-center"
                            ),
                            Input(
                                type="range", name="keep_end_fraction", id="keep-end-slider",
                                min="0", max="1", step="0.01", value=default_end_fraction,
                                cls="range range-info range-xs w-full",
                                oninput="document.getElementById('keep-end-value').textContent = Math.round(this.value*1000)/10 + '%'"
                            ),
                            Div("How much of the end of each censored word stays audible", cls="text-xs opacity-60"),
                            cls="mt-3"
                        ),
                    ),
                    Div(
                        Label(
                            Input(type="checkbox", id="range-enable-checkbox", name="range_enabled", cls="checkbox checkbox-sm", disabled=True),
                            Span("Only censor within a specific range", cls="ml-2"),
                            cls="flex items-center cursor-pointer"
                        ),
                        Div("📎 Select a file above to enable a specific in/out censoring range", id="range-no-file-hint", cls="text-xs opacity-60 mt-1"),
                        cls="mt-4"
                    ),
                    Div(
                        Div(
                            Video(id="range-preview-video", controls=True, cls="w-full rounded-lg bg-black max-h-[45vh]"),
                            cls="mt-3"
                        ),
                        Div(
                            Label(
                                Input(type="checkbox", id="range-beep-only-checkbox", name="beep_only_in_range", cls="checkbox checkbox-sm"),
                                Span("Only apply sound effect within range", cls="ml-2"),
                                cls="flex items-center cursor-pointer"
                            ),
                            cls="mt-3"
                        ),
                        Div(
                            Div(
                                Span("0:00", id="range-label-in", cls="text-xs font-mono"),
                                Span("0:00", id="range-label-out", cls="text-xs font-mono"),
                                cls="flex justify-between mb-1"
                            ),
                            Div(
                                Div(id="range-fill", cls="absolute h-full bg-info/60 rounded"),
                                Div(id="range-handle-in", cls="absolute w-4 h-4 rounded-full bg-info border-2 border-white shadow top-1/2 -translate-y-1/2 -translate-x-1/2 cursor-pointer touch-none"),
                                Div(id="range-handle-out", cls="absolute w-4 h-4 rounded-full bg-info border-2 border-white shadow top-1/2 -translate-y-1/2 -translate-x-1/2 cursor-pointer touch-none"),
                                id="range-slider-track", cls="relative w-full h-3 bg-base-300 rounded mt-1"
                            ),
                            cls="mt-3"
                        ),
                        Input(type="hidden", name="range_start", id="range-start-input", value="0"),
                        Input(type="hidden", name="range_end", id="range-end-input", value="0"),
                        id="range-controls-wrapper", cls="opacity-50 pointer-events-none transition-opacity duration-200"
                    ),
                    Script(NotStr("""
(function() {
    const fileInput = document.getElementById('video-file-input');
    const previewVideo = document.getElementById('range-preview-video');
    const enableCb = document.getElementById('range-enable-checkbox');
    const wrapper = document.getElementById('range-controls-wrapper');
    const track = document.getElementById('range-slider-track');
    const handleIn = document.getElementById('range-handle-in');
    const handleOut = document.getElementById('range-handle-out');
    const fill = document.getElementById('range-fill');
    const labelIn = document.getElementById('range-label-in');
    const labelOut = document.getElementById('range-label-out');
    const startInput = document.getElementById('range-start-input');
    const endInput = document.getElementById('range-end-input');
    const noFileHint = document.getElementById('range-no-file-hint');
    if (!fileInput || !enableCb) return;

    let duration = 0;
    let inTime = 0;
    let outTime = 0;

    function formatTime(t) {
        t = Math.max(0, Math.floor(t));
        const m = Math.floor(t / 60);
        const s = t % 60;
        return m + ':' + String(s).padStart(2, '0');
    }

    function updateUI() {
        if (!duration) return;
        const pctIn = (inTime / duration) * 100;
        const pctOut = (outTime / duration) * 100;
        handleIn.style.left = pctIn + '%';
        handleOut.style.left = pctOut + '%';
        fill.style.left = pctIn + '%';
        fill.style.width = Math.max(pctOut - pctIn, 0) + '%';
        labelIn.textContent = formatTime(inTime);
        labelOut.textContent = formatTime(outTime);
        startInput.value = inTime.toFixed(2);
        endInput.value = outTime.toFixed(2);
    }

    function setEnabled(isEnabled) {
        if (isEnabled) {
            wrapper.classList.remove('opacity-50', 'pointer-events-none');
        } else {
            wrapper.classList.add('opacity-50', 'pointer-events-none');
        }
    }

    function handleFileSelected() {
        if (!fileInput.files || !fileInput.files[0]) return;
        const url = URL.createObjectURL(fileInput.files[0]);
        previewVideo.src = url;
        enableCb.disabled = false;
        if (noFileHint) noFileHint.style.display = 'none';
        previewVideo.addEventListener('loadedmetadata', function onMeta() {
            duration = previewVideo.duration || 0;
            inTime = 0;
            outTime = duration;
            updateUI();
            previewVideo.removeEventListener('loadedmetadata', onMeta);
        });
    }

    fileInput.addEventListener('change', handleFileSelected);
    handleFileSelected();

    enableCb.addEventListener('change', function() {
        setEnabled(enableCb.checked);
    });

    let dragging = null;

    function posToTime(clientX) {
        const rect = track.getBoundingClientRect();
        let pct = (clientX - rect.left) / rect.width;
        pct = Math.min(Math.max(pct, 0), 1);
        return pct * duration;
    }

    function startDrag(which) {
        return function(e) {
            if (!enableCb.checked || !duration) return;
            dragging = which;
            e.preventDefault();
        };
    }

    handleIn.addEventListener('pointerdown', startDrag('in'));
    handleOut.addEventListener('pointerdown', startDrag('out'));

    window.addEventListener('pointermove', function(e) {
        if (!dragging) return;
        const t = posToTime(e.clientX);
        if (dragging === 'in') {
            inTime = Math.max(0, Math.min(t, outTime));
            previewVideo.currentTime = inTime;
        } else {
            outTime = Math.min(duration, Math.max(t, inTime));
            previewVideo.currentTime = outTime;
        }
        updateUI();
    });

    window.addEventListener('pointerup', function() {
        dragging = null;
    });
})();
""")),
                    Textarea(DEFAULTS.get("default_censor_words", ""), type="text", name="censor_words", placeholder="🗣️ Enter words to censor", cls="input input-bordered w-full mt-5"),
                    Div("Supported URL platforms: YouTube, Facebook, Instagram, TikTok", cls="text-sm opacity-70 mt-2 mb-2"),
                    Button("🤫 Censor Now", cls="btn btn-info mt-5"),
                    hx_post="/censor_start",
                    hx_target="this",
                    hx_swap="outerHTML",
                    hx_indicator="#htmx-indicator",
                    enctype="multipart/form-data",
                ),
                cls="card-body"
            ),
            cls="card bg-base-300 hero-content text-center mx-auto my-auto w-full lg:max-w-1/2 justify-center"
        ),
        cls="hero min-h-screen mb-0 rounded-b-3xl"
    )

    second_hero = Div(
        Div(
            Div("About CensorUp", cls="text-3xl font-bold mb-2"),
            cls="card bg-base-300 hero-content text-left mx-auto my-auto w-full max-w-4xl p-6"
        ),
        cls="hero bg-base-300 min-h-screen mb-0 rounded-b-3xl"
    )

    htmx_indicator = Div(
        Div(
            Div(cls="loading loading-lg"),
            Div("Processing...", cls="mt-3 text-white"),
            cls="text-center"
        ),
        id="htmx-indicator",
        cls="htmx-indicator fixed inset-0 bg-black/50 flex items-center justify-center z-50"
    )

    return Title("CensorUp — Automatic Profanity Censorship, No Signup Required"), Div(
        Head(Defaults, navbar),
        Body(Main(first_hero, second_hero, htmx_indicator), data_theme="silk", cls="bg-base-200")
    )


def render_processing_card(job_id: str, stage: str, progress: float):
    pct = max(0, min(100, int(progress)))
    return Div(
        Div(
            Div(cls="loading loading-lg loading-spinner text-info"),
            Div(stage, cls="mt-3 font-medium"),
            Progress(value=str(pct), max="100", cls="progress progress-info w-full mt-4"),
            Div(f"{pct}%", cls="text-xs opacity-70 mt-1"),
            cls="text-center py-10 max-w-md mx-auto"
        ),
        id=f"job-{job_id}",
        hx_get=f"/censor_status/{job_id}",
        hx_trigger="load, every 1s",
        hx_swap="outerHTML",
    )


def render_result(result_data: dict):
    censored_file = result_data["path"]
    mute_ranges = result_data.get("mute_ranges", [])
    duration = result_data.get("duration", 0)
    download_filename = os.path.basename(censored_file)

    timeline_data = json.dumps(mute_ranges)

    preview_script = Script(f"""
    (function() {{
       const ranges = {timeline_data};
       const duration = {duration};
       const track = document.getElementById('timeline-track');
       const playhead = document.getElementById('playhead');
       const video = document.getElementById('preview-video');
       if (!track || !video || !duration) return;

       ranges.forEach(function(r) {{
           const block = document.createElement('div');
           block.className = 'absolute top-0 h-full bg-error/70';
           block.style.left = (r[0] / duration * 100) + '%';
           block.style.width = Math.max((r[1] - r[0]) / duration * 100, 0.3) + '%';
           block.title = '"' + r[2] + '"  (' + r[0].toFixed(2) + 's - ' + r[1].toFixed(2) + 's)';
           track.appendChild(block);
       }});

       video.addEventListener('timeupdate', function() {{
           const pct = (video.currentTime / duration) * 100;
           playhead.style.left = pct + '%';
       }});

       function seekToClientX(clientX) {{
           const rect = track.getBoundingClientRect();
           const pct = Math.min(Math.max((clientX - rect.left) / rect.width, 0), 1);
           video.currentTime = pct * duration;
       }}

       let scrubbing = false;
       let wasPlaying = false;

       track.addEventListener('pointerdown', function(e) {{
           scrubbing = true;
           wasPlaying = !video.paused;
           if (wasPlaying) video.pause();  // avoid fighting playback while dragging
           track.setPointerCapture(e.pointerId);  // keeps tracking even if the
                                                    // pointer moves outside the bar
           seekToClientX(e.clientX);
       }});

       track.addEventListener('pointermove', function(e) {{
           if (!scrubbing) return;
           seekToClientX(e.clientX);
       }});

       function stopScrubbing(e) {{
           if (!scrubbing) return;
           scrubbing = false;
           if (wasPlaying) video.play();
       }}
       track.addEventListener('pointerup', stopScrubbing);
       track.addEventListener('pointercancel', stopScrubbing);
    }})();
    """)

    preview_section = Div(
       Div("🎬 Preview", cls="text-lg font-semibold mt-6 mb-2"),
       Video(id="preview-video", src=f"/download/{download_filename}", controls=True, cls="w-full rounded-lg bg-black max-h-[60vh]"),
       Div(
           Div(id="playhead", cls="absolute top-0 h-full w-[2px] bg-info z-10", style="left:0%"),
           id="timeline-track",
           cls="relative w-full h-8 bg-base-300 rounded mt-3 cursor-pointer overflow-hidden"
       ),
       Div(f"Red blocks mark censored sections ({len(mute_ranges)} total) — hover one to see the word, click the bar to jump around.", cls="text-xs opacity-70 mt-2"),
       preview_script,
       cls="w-full max-h-[70vh] overflow-y-auto"
    )

    return Div(
       Div(
           A("📼 Download Censored Video", cls="btn btn-success", href=f"/download/{download_filename}"),
           A("Refresh Site", cls="btn btn-soft btn-error ml-4", href="/"),
       ),
       preview_section,
    )


def run_censor_job(job_id: str, file_path, pending_url, words_censor_list, beep_path,
                   range_start, range_end, beep_only_in_range, keep_start_fraction, keep_end_fraction):
    try:
        if pending_url:
            with JOBS_LOCK:
                JOBS[job_id]["stage"] = "🌐 Downloading from URL..."
            try:
                file_path = download_video(pending_url)
            except ValueError as e:
                with JOBS_LOCK:
                    JOBS[job_id]["status"] = "error"
                    JOBS[job_id]["error"] = str(e)
                return

        with JOBS_LOCK:
            JOBS[job_id]["stage"] = "🗣️ Transcribing & finding censored words..."

        def progress_cb(current, total):
            pct = round(current / total * 100, 1) if total else 0
            with JOBS_LOCK:
                if job_id in JOBS:
                    JOBS[job_id]["progress"] = pct

        result_data = censor_media(
            "small", file_path, words_censor_list, beep_file=beep_path, progress_callback=progress_cb,
            range_start=range_start, range_end=range_end, beep_only_in_range=beep_only_in_range,
            keep_start_fraction=keep_start_fraction, keep_end_fraction=keep_end_fraction
        )

        with JOBS_LOCK:
            if job_id not in JOBS:
                return
            if not result_data:
                try:
                    os.remove(file_path)
                except OSError:
                    pass
                JOBS[job_id]["status"] = "no_match"
            else:
                JOBS[job_id]["status"] = "done"
                JOBS[job_id]["result"] = result_data
    except Exception as e:
        with JOBS_LOCK:
            if job_id in JOBS:
                JOBS[job_id]["status"] = "error"
                JOBS[job_id]["error"] = str(e)


@rt('/censor_start')
async def post(file: Optional[UploadFile] = None, beep_file: Optional[UploadFile] = None, url: str = "", censor_words: str = "",
               range_enabled: Optional[str] = None, beep_only_in_range: Optional[str] = None,
               range_start: str = "0", range_end: str = "0",
               keep_start_fraction: str = "0.225", keep_end_fraction: str = "0.12"):
    pending_url = None
    if file is not None and file.filename:
        file_bytes = file.file.read()
        file_name = file.filename
        with open(os.path.join("uploads", file_name), "wb") as f:
            f.write(file_bytes)
        file_path = os.path.join("uploads", file_name)
    elif url != "" and (file is None or not file.filename):
        file_path = None
        pending_url = url
    else:
        return Div(
            Div("⚠️ Please provide a file or a URL to censor.", cls="alert alert-error"),
            A("Refresh Site", cls="btn btn-soft btn-error mt-3", href="/")
        )

    beep_path = None
    if beep_file is not None and beep_file.filename:
        beep_path = os.path.join("uploads", beep_file.filename)
        with open(beep_path, "wb") as f:
            f.write(beep_file.file.read())
    elif DEFAULTS.get("default_sound_effect") and os.path.isfile(DEFAULTS["default_sound_effect"]):
        beep_path = DEFAULTS["default_sound_effect"]

    words_censor_list = [w.strip().lower() for w in censor_words.split(",") if w.strip()]

    range_is_enabled = range_enabled is not None
    range_start_val, range_end_val = None, None
    if range_is_enabled:
        try:
            range_start_val = float(range_start)
            range_end_val = float(range_end)
            if range_end_val <= range_start_val:
                range_is_enabled = False
        except ValueError:
            range_is_enabled = False

    beep_only_flag = beep_only_in_range is not None and range_is_enabled

    try:
        keep_start_val = min(max(float(keep_start_fraction), 0.0), 1.0)
    except ValueError:
        keep_start_val = 0.225
    try:
        keep_end_val = min(max(float(keep_end_fraction), 0.0), 1.0)
    except ValueError:
        keep_end_val = 0.12

    job_id = uuid.uuid4().hex
    initial_stage = "🌐 Downloading from URL..." if pending_url else "🗣️ Transcribing & finding censored words..."
    with JOBS_LOCK:
        JOBS[job_id] = {
            "status": "processing",
            "stage": initial_stage,
            "progress": 0.0,
            "result": None,
            "error": None
        }

    threading.Thread(
        target=run_censor_job,
        args=(
            job_id, file_path, pending_url, words_censor_list, beep_path,
            range_start_val if range_is_enabled else None,
            range_end_val if range_is_enabled else None,
            beep_only_flag, keep_start_val, keep_end_val
        ),
        daemon=True
    ).start()

    return render_processing_card(job_id, initial_stage, 0)


@rt('/censor_status/{job_id}')
def get(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        return Div(
            Div("⚠️ Job not found or expired.", cls="alert alert-error"),
            A("Refresh Site", cls="btn btn-soft btn-error mt-3", href="/")
        )
    if job["status"] == "processing":
        return render_processing_card(job_id, job["stage"], job["progress"])
    if job["status"] == "error":
        with JOBS_LOCK:
            JOBS.pop(job_id, None)
        return Div(
            Div(f"⚠️ {job['error']}", cls="alert alert-error"),
            A("Refresh Site", cls="btn btn-soft btn-error mt-3", href="/")
        )
    if job["status"] == "no_match":
        with JOBS_LOCK:
            JOBS.pop(job_id, None)
        return Div(
            Div("✅ No blocked words found in that file.", cls="alert alert-warning"),
            A("Refresh Site", cls="btn btn-soft btn-warning mt-3", href="/")
        )
    result_data = job["result"]
    with JOBS_LOCK:
        JOBS.pop(job_id, None)
    return render_result(result_data)


@rt('/download/{filename:path}')
def get(filename: str):
    file_path = os.path.join("uploads", filename)
    if not os.path.isfile(file_path):
        return Div("⚠️ File not found.", cls="alert alert-error")
    return FileResponse(file_path, filename=filename)


# --- Background Threads ---
threading.Thread(target=delete_old_local_files, daemon=True).start()
threading.Thread(target=watch_for_page_close, daemon=True).start()
threading.Thread(target=run_tray_once_ready, daemon=True).start()


# --- Thread-Safe Dynamic Browser Opener ---
def wait_for_server_and_launch(port=5001):
    """Checks the port loop until the application server responds, then triggers Edge App Mode."""
    timeout = 20
    start_time = time.time()
    while time.time() - start_time < timeout:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            if sock.connect_ex(('127.0.0.1', port)) == 0:
                edge_cmd = f'start msedge --app="http://127.0.0.1:{port}"'
                try:
                    os.system(edge_cmd)
                except Exception:
                    webbrowser.open(f"http://127.0.0.1:{port}")
                return
        time.sleep(0.2)


# Start background web viewer right before spawning uvicorn
threading.Thread(target=wait_for_server_and_launch, args=(PORT,), daemon=True).start()


# --- Main Uvicorn Server Anchor ---
import uvicorn
if __name__ == "__main__":
    # log_config=None skips uvicorn's own logging setup (which used to
    # crash on isatty checks in windowed mode) - but since we already
    # attached a file handler to the root logger above, uvicorn's own
    # loggers (including unhandled-exception logging) still propagate to
    # it and land in logs\censorup.log.
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_config=None)