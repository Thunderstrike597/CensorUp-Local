from fasthtml.common import *
from fasthtml.components import *
from censor_script import censor_media
from url_downloader_script import download_video
import os, threading, time, json, uuid
from typing import Optional
import base64
from urllib.parse import urlparse
from starlette.responses import FileResponse

# Initialize FastHTML App
app = FastHTML()
rt = app.route
os.makedirs("uploads", exist_ok=True)

# --- Background job tracking (for the live progress bar) ---
# job -> {"status": "processing"|"done"|"no_match"|"error", "stage": str,
#         "progress": 0-100, "result": dict|None, "error": str|None}
JOBS = {}
JOBS_LOCK = threading.Lock()

# --- Defaults (pre-filled censor words + fallback sound effect) ---
def load_defaults():
    try:
        with open("defaults.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"default_sound_effect": "", "default_censor_words": ""}

DEFAULTS = load_defaults()

# --- Local storage helpers (replaces the old Pocketbase upload/cloud storage) ---

def delete_old_local_files():
    """Background loop: deletes files in uploads/ older than 1 hour, same
    privacy behavior as before but kept fully local instead of Pocketbase."""
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
    Link(
        rel="stylesheet",
        href="https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@latest/dist/tabler-icons.min.css"
    ),
    Link(href="https://cdn.jsdelivr.net/npm/daisyui@5", rel="stylesheet", type="text/css"),
    Link(href="https://cdn.jsdelivr.net/npm/daisyui@5.0.0/themes.css", rel="stylesheet", type="text/css"),
    Script(src="https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4"),
    # htmx for hx- attributes
    Script(src="https://cdn.jsdelivr.net/npm/htmx.org@2.0.8/dist/htmx.min.js"),
    Script(src="https://cloud.umami.is/script.js" ,data_website_id="1cdc08e2-77c1-4c03-b680-9e7640e41616"),
    # SEO meta tags
    Title("CensorUp — Automatic Profanity Censorship, No Signup Required"),
    Meta(name="viewport", content="width=device-width, initial-scale=1"),
    Meta(name="description", content="CensorUp automatically censors profanity and unwanted words from audio and video. Upload or link your media, provide words to censor, and download the censored result."),
    Meta(name="keywords", content="censor, audio censor, video censor, profanity filter, content moderation, automatic censoring"),
    Meta(property="og:title", content="CensorUp — Automatic Audio/Video Profanity Censoring"),
    Meta(property="og:description", content="Upload or link audio/video and automatically censor specified words. Fast processing, flexible inputs, privacy-focused."),
    Meta(property="og:type", content="website"),
    Meta(name="twitter:card", content="summary_large_image"),
)

@rt('/')
def get(sess):
 
 navbar = Div(
                Div(
                    "🤫 CensorUp", 
                    cls="text-2xl text-info font-bold navbar-start"
                ),
                Div(
                    A(I(cls="ti ti-brand-github-filled text-lg")," Github", href="https://github.com/Anas099X", cls="btn"),
                    cls="text-warning font-bold navbar-end space-x-5 drop-shadow-[0_1.2px_1.2px_rgba(0,0,0,0.8)]"
                ),
                cls="navbar bg-ghost py-2 fixed z-50"  # reduced navbar padding
            )   

 first_hero = Div(

    # Main content
    Div(
        Div(
            Div("🎬 Upload your Audio/Video File to Censor it Automatically!", cls="text-2xl font-bold text-netural mb-4"),
            Form(    
            Div(
                Input(type="file", name="beep_file", cls="file-input file-input-sm", accept="audio/*"),
                Div(
                    "🔊 Optional: sound effect to play instead of silence — leave empty to use the default"
                    if DEFAULTS.get("default_sound_effect") and os.path.isfile(DEFAULTS["default_sound_effect"])
                    else "🔊 Optional: sound effect to play instead of silence (leave empty for a normal mute)",
                    cls="text-xs opacity-70 ml-2 self-center"
                ),
                cls="flex w-full flex-col lg:flex-row items-start lg:items-center mb-3"
            ),
            Div(
                Input(type="file" ,name="file",cls="file-input"),
                Div("OR",cls="divider lg:divider-horizontal"),
                Input(type="text", name="url",placeholder="🌐 Enter a YouTube / Facebook / Instagram / TikTok URL",cls="input input-bordered w-full"),
                cls="flex w-full flex-col lg:flex-row"
            ),
            Textarea(DEFAULTS.get("default_censor_words", ""), type="text", name="censor_words",placeholder="🗣️ Enter words to censor seperated by commas (ex. badword1,badword2)",cls="input input-bordered w-full mt-5"),
            Div("Supported URL platforms: YouTube, Facebook, Instagram, TikTok", cls="text-sm opacity-70 mt-2 mb-2"),
            Button("🤫 Censor Now",cls="btn btn-info mt-5"),
            hx_post="/censor_start",
            hx_target="this",
            hx_swap="outerHTML",
            # connect the indicator (kept for clarity; event listeners also handle show/hide)
            hx_indicator="#htmx-indicator",
            enctype="multipart/form-data",
            ),
            cls="card-body"
        ),
        cls="card bg-base-300 hero-content text-center mx-auto my-auto w-full lg:max-w-1/2 justify-center"
    ),
    cls="hero min-h-screen mb-0 rounded-b-3xl")
 

 second_hero = Div(

    # SEO-friendly About + Features + Q&A
    Div(
        Div("About CensorUp", cls="text-3xl font-bold mb-2"),
        Div(
            "CensorUp helps you automatically remove or mask profanity and undesired words from audio and video. "
            "Upload a file or provide a direct URL, list words to censor, and get a downloadable censored media file. "
            "Designed for content creators, educators, and platforms that want a quick, private way to sanitize media.",
            cls="text-base mb-5 w-full mx-auto"
        ),

        # Features grid
        Div(
            Div(
                Div("⚡ Fast & Accurate", cls="text-lg font-semibold"),
                Div("AI-assisted transcription and precise censoring with minimal manual work.", cls="text-sm"),
                cls="card p-4"
            ),
            Div(
                Div("🔗 Flexible Inputs", cls="text-lg font-semibold"),
                Div("Upload files or provide a direct URL to media hosted elsewhere.", cls="text-sm"),
                cls="card p-4"
            ),
            Div(
                Div("🔒 Privacy-first", cls="text-lg font-semibold"),
                Div("No Signup Required. Files are processed and then automatically removed within hour of upload; no unnecessary retention.", cls="text-sm"),
                cls="card p-4"
            ),
            cls="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6"
        ),

        # Q&A / FAQ section
        Div(
            Div("Frequently Asked Questions", cls="text-xl font-semibold mb-3"),
            Div(
                Div(Div("Q: How long does processing take?", cls="font-bold"), Div("A: Small files usually process within seconds to a minute. Larger files depend on duration and server load.", cls="mb-4")),
                Div(Div("Q: What formats are supported?", cls="font-bold"), Div("A: Common audio/video formats (mp3, wav, mp4, mov). If you provide a direct URL, CensorUp will attempt to fetch supported media.", cls="mb-4")),
                Div(Div("Q: Are my files kept?", cls="font-bold"), Div("A: By default, files are stored temporarily for processing and automatically removed after completion with an hour. Do not upload sensitive data you are not comfortable sharing.", cls="mb-4")),
                cls="prose max-w-none"
            ),
            cls="mb-6"
        ),
        cls="card bg-base-300 hero-content text-left mx-auto my-auto w-full max-w-4xl p-6"
    ),
    cls="hero bg-base-300 min-h-screen mb-0 rounded-b-3xl"
)
 

 # simple full-screen indicator (hidden by default; shown by htmx events)
 htmx_indicator = Div(
        Div(
            Div(cls="loading loading-lg"),  # daisyui spinner
            Div("Processing...", cls="mt-3 text-white"),
            cls="text-center"
        ),
        id="htmx-indicator",
        cls="htmx-indicator fixed inset-0 bg-black/50 flex items-center justify-center z-50"
    )

 return Title("CensorUp — Automatic Profanity Censorship, No Signup Required"),Div(
            Head(Defaults,navbar),
            
            Body(
                Main(
                    first_hero,
                    second_hero,
                    htmx_indicator,  # include indicator in page
                ),
                data_theme="silk",
                cls="bg-base-200"
            )
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
    mute_ranges = result_data["mute_ranges"]
    duration = result_data["duration"]
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

       track.addEventListener('click', function(e) {{
           const rect = track.getBoundingClientRect();
           const pct = Math.min(Math.max((e.clientX - rect.left) / rect.width, 0), 1);
           video.currentTime = pct * duration;
       }});
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
           A("📼 Download Censored Video",cls="btn btn-success",href=f"/download/{download_filename}"),
           A("Refresh Site",cls="btn btn-soft btn-error ml-4",href="/"),
       ),
       preview_section,
    )


def run_censor_job(job_id: str, file_path, pending_url, words_censor_list, beep_path):
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

        result_data = censor_media("small", file_path, words_censor_list, beep_file=beep_path, progress_callback=progress_cb)

        with JOBS_LOCK:
            if job_id not in JOBS:
                return  # job was already cleaned up / abandoned
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
async def post(file: Optional[UploadFile] = None, beep_file: Optional[UploadFile] = None, url: str = "", censor_words: str = ""):

 pending_url = None

 if file is not None and file.filename:
  #read + save input file synchronously (fast) — the slow work happens in the background job
  file_bytes = file.file.read()
  file_name = file.filename

  with open(os.path.join("uploads", file_name), "wb") as f:
    f.write(file_bytes)

  file_path = os.path.join("uploads", file_name)
 elif url != "" and (file is None or not file.filename):
  file_path = None
  pending_url = url
 else:
   return Div(Div("⚠️ Please provide a file or a URL to censor.", cls="alert alert-error"),A("Refresh Site",cls="btn btn-soft btn-error mt-3",href="/"))   

 #save the optional sound-effect file, if one was actually chosen
 beep_path = None
 if beep_file is not None and beep_file.filename:
   beep_path = os.path.join("uploads", beep_file.filename)
   with open(beep_path, "wb") as f:
     f.write(beep_file.file.read())
 elif DEFAULTS.get("default_sound_effect") and os.path.isfile(DEFAULTS["default_sound_effect"]):
   # no sound effect uploaded — fall back to the configured default, if it actually exists on disk
   beep_path = DEFAULTS["default_sound_effect"]

 #censor words list
 words_censor_list = [w.strip().lower() for w in censor_words.split(",") if w.strip()]
 print(words_censor_list)

 job_id = uuid.uuid4().hex
 initial_stage = "🌐 Downloading from URL..." if pending_url else "🗣️ Transcribing & finding censored words..."
 with JOBS_LOCK:
   JOBS[job_id] = {"status": "processing", "stage": initial_stage, "progress": 0.0, "result": None, "error": None}

 threading.Thread(target=run_censor_job, args=(job_id, file_path, pending_url, words_censor_list, beep_path), daemon=True).start()

 return render_processing_card(job_id, initial_stage, 0)


@rt('/censor_status/{job_id}')
def get(job_id: str):
 with JOBS_LOCK:
   job = JOBS.get(job_id)

 if not job:
   return Div(Div("⚠️ Job not found or expired.", cls="alert alert-error"),A("Refresh Site",cls="btn btn-soft btn-error mt-3",href="/"))

 if job["status"] == "processing":
   return render_processing_card(job_id, job["stage"], job["progress"])

 if job["status"] == "error":
   with JOBS_LOCK:
     JOBS.pop(job_id, None)
   return Div(Div(f"⚠️ {job['error']}", cls="alert alert-error"), A("Refresh Site",cls="btn btn-soft btn-error mt-3",href="/"))

 if job["status"] == "no_match":
   with JOBS_LOCK:
     JOBS.pop(job_id, None)
   return Div(Div("✅ No blocked words found in that file — nothing to censor.", cls="alert alert-warning"),A("Refresh Site",cls="btn btn-soft btn-warning mt-3",href="/"))

 # done
 result_data = job["result"]
 with JOBS_LOCK:
   JOBS.pop(job_id, None)
 return render_result(result_data)

@rt('/download/{filename:path}')
def get(filename: str):
 #serves files straight from the local uploads/ folder
 file_path = os.path.join("uploads", filename)
 if not os.path.isfile(file_path):
   return Div("⚠️ File not found or already expired.", cls="alert alert-error")
 return FileResponse(file_path, filename=filename)

#start background cleanup thread (deletes uploads/ files older than 1hr, replaces old Pocketbase auto-delete)
threading.Thread(target=delete_old_local_files, daemon=True).start()

serve()