import os, subprocess, threading
import whisper_timestamped as whisper
import tqdm as _tqdm_pkg
import whisper.transcribe as _openai_whisper_transcribe  # the real openai-whisper package (unrelated to the "whisper" alias above)
import sys

def _ffmpeg_bin_dir():
    """Return the folder that contains ffmpeg.exe / ffprobe.exe."""
    if getattr(sys, 'frozen', False):
        # Running as a PyInstaller one-file executable
        return sys._MEIPASS
    else:
        # Running from source
        return os.path.join(os.path.dirname(__file__), "assets", "ffmpeg")

def get_ffmpeg():
    return os.path.join(_ffmpeg_bin_dir(), "ffmpeg.exe")

def get_ffprobe():
    return os.path.join(_ffmpeg_bin_dir(), "ffprobe.exe")

# Make sure Whisper (and any other library) can also find ffmpeg when frozen
if getattr(sys, 'frozen', False):
    os.environ["PATH"] = sys._MEIPASS + os.pathsep + os.environ.get("PATH", "")

#os.environ["PATH"] += os.pathsep + r"C:\Users\Anas\ffmpeg\bin"
 
# --- Progress reporting ---
# whisper_timestamped calls openai-whisper's model.transcribe() internally, which
# shows a tqdm progress bar ("frames/s" in the console). We subclass tqdm so that,
# while a callback is registered for the current thread, every .update() also
# reports (current, total) through it — without changing the normal console output.
_progress_local = threading.local()
 
class _ProgressReportingTqdm(_tqdm_pkg.tqdm):
    def update(self, n=1):
        displayed = super().update(n)
        cb = getattr(_progress_local, "callback", None)
        if cb and self.total:
            cb(self.n, self.total)
        return displayed
 
def _patch_whisper_progress_bar():
    """Globally patches tqdm modules in Python's cache system 
    so that whisper, openai-whisper, and whisper_timestamped all use our reporter.
    """
    import sys
    
    # 1. Patch the main tqdm class directly
    try:
        _tqdm_pkg.tqdm = _ProgressReportingTqdm
    except Exception:
        pass

    # 2. Force it into OpenAI Whisper's module reference
    try:
        _openai_whisper_transcribe.tqdm = _ProgressReportingTqdm
        _openai_whisper_transcribe.tqdm.tqdm = _ProgressReportingTqdm
    except Exception:
        pass

    # 3. Aggressively target Python's module cache for any internal tqdm aliases
    try:
        if 'tqdm' in sys.modules:
            sys.modules['tqdm'].tqdm = _ProgressReportingTqdm
            sys.modules['tqdm']._tqdm = _ProgressReportingTqdm
        
        # Target whisper_timestamped's underlying transcription engine module
        import whisper_timestamped.transcribe as _wt_transcribe
        _wt_transcribe.tqdm = _ProgressReportingTqdm
        if hasattr(_wt_transcribe, 'tqdm_pkg'):
            _wt_transcribe.tqdm_pkg.tqdm = _ProgressReportingTqdm
            
        print("🎯 Successfully hooked into global and whisper_timestamped progress bars!")
    except Exception as e:
        print(f"⚠️ Could not hook into whisper's internal progress bar: {e}")

_patch_whisper_progress_bar()
 
 
def get_media_duration(path: str) -> float:
    """Returns duration in seconds via ffprobe (ships alongside ffmpeg)."""
    result = subprocess.run(
        [get_ffprobe(), "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, check=True
    )
    return float(result.stdout.strip())
 
 
def censor_media(model_type: str, input_media: str, blocked_words: list, beep_file: str = None, progress_callback=None,
                  range_start: float = None, range_end: float = None, beep_only_in_range: bool = False,
                  keep_start_fraction: float = 0.225, keep_end_fraction: float = 0.12):
    """Censors blocked words. Returns a dict {"path", "mute_ranges", "duration"}
    on success, or None if no blocked words were found. mute_ranges is a list
    of (start, end, word) tuples in seconds — used to render the preview timeline.
    If beep_file is given, that sound plays during the muted ranges instead of
    silence; otherwise it's a plain mute like before.
    progress_callback(current, total), if given, is called repeatedly during
    transcription (the slowest step) so a caller can track percent-complete.

    range_start/range_end (seconds), if both given, restrict censoring to that
    window — words outside it are left alone entirely — UNLESS beep_only_in_range
    is also True, in which case the WHOLE video still gets censored as normal,
    but only words inside the range get the beep sound; words outside the range
    are muted in plain silence even if a beep_file is set.
    """
 
    # Load Whisper model
    model = whisper.load_model(model_type)
 
    media = input_media
 
    _progress_local.callback = progress_callback
    try:
        result = whisper.transcribe(model, media, language="en")
    finally:
        _progress_local.callback = None
    
    #print(result)
    # Words you want to remove
    
    # --- Partial censoring settings ---
    # KEEP_START_FRACTION: how much of the word's duration to leave audible at the START (e.g. the "f" in "fuck")
    # KEEP_END_FRACTION: how much of the word's duration to leave audible at the END (e.g. the "k" in "f**k") — set to 0 to mute all the way through
    KEEP_START_FRACTION = keep_start_fraction
    KEEP_END_FRACTION = keep_end_fraction
    MIN_KEEP_SECONDS = 0.06   # don't bother keeping a sliver shorter than this (very short words)

    range_enabled = range_start is not None and range_end is not None and range_end > range_start
    effective_beep_only_in_range = bool(beep_only_in_range) and range_enabled
 
    # Collect mute ranges
    mute_ranges = []
    for segment in result["segments"]:
        for word in segment["words"]:
            clean_word = word["text"].strip().strip('.!?,":;`').strip().lower()
            if clean_word in blocked_words:
                word_in_range = True
                if range_enabled:
                    word_in_range = range_start <= word["start"] <= range_end
                    if not effective_beep_only_in_range and not word_in_range:
                        # scope-restricted mode: word falls outside the selected
                        # range, so it's left alone entirely (not muted at all)
                        continue

                word_duration = word["end"] - word["start"]
 
                keep_start = word_duration * KEEP_START_FRACTION
                keep_end = word_duration * KEEP_END_FRACTION
                if keep_start < MIN_KEEP_SECONDS:
                    keep_start = 0
                if keep_end < MIN_KEEP_SECONDS:
                    keep_end = 0
 
                start = max(0, word["start"] + keep_start - 0.02)   # tiny 20ms padding so the cut isn't razor-sharp
                end = word["end"] - keep_end + 0.02
 
                if end <= start:
                    # word too short for the fractions above — fall back to muting the whole thing
                    start = max(0, word["start"] - 0.15)
                    end = word["end"] + 0.18
 
                mute_ranges.append((
                    float(start),
                    float(end),
                    clean_word,
                    bool(word_in_range),
                ))
                print(f"Muted word: {word['text']} timeline: {start}-{end}")
 
    if not mute_ranges:
        transcript = " ".join(w["text"].strip() for seg in result["segments"] for w in seg["words"])
        print("✅ No blocked words found. Skipping censorship.")
        print(f"📝 Here's what Whisper actually heard, in case a word was misheard:\n{transcript}")
        return
 
    print("Mute ranges:", mute_ranges)
 
    duration = get_media_duration(media)
 
    #✅ Create truly separate output file in same folder
    folder = os.path.dirname(media)
    filename = os.path.basename(media)
    name, ext = os.path.splitext(filename)
    output_file = os.path.join(folder, f"{name}_censored{ext}")
 
    print(f"Input file: {media}")
    print(f"Output file: {output_file}")
 
    # Build the FFmpeg audio filter — this mutes EVERY entry in mute_ranges,
    # regardless of beep mode (beep mode only decides which of these get the
    # sound effect mixed in on top; the muting itself always covers all of them)
    volume_expr = " + ".join([f"between(t,{start},{end})" for start, end, _word, _in_range in mute_ranges])
    volume_filter = f"volume=enable='{volume_expr}':volume=0"
 
    use_beep = beep_file and os.path.isfile(beep_file)

    # Which mute_ranges indices should get the sound effect mixed in?
    # - No beep file at all: none.
    # - Beep file, but not in "beep-only-in-range" mode: every muted range gets it
    #   (this also covers the "scope-restricted" range mode, since in that case
    #   mute_ranges already only contains in-range words to begin with).
    # - Beep file AND beep_only_in_range: only the ones actually inside the range.
    if not use_beep:
        beep_indices = []
    elif effective_beep_only_in_range:
        beep_indices = [i for i, (_s, _e, _w, in_rng) in enumerate(mute_ranges) if in_rng]
    else:
        beep_indices = list(range(len(mute_ranges)))
 
    if beep_indices:
        # Mute the original audio as before, then mix in the sound effect
        # (looped/trimmed to exactly fill each muted window) during the
        # beep-eligible ranges only — any other muted ranges stay plain silence.
        filter_parts = [
            f"[0:a]{volume_filter},aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[muted]"
        ]
        mix_inputs = ["[muted]"]
        for i in beep_indices:
            start, end, _word, _in_range = mute_ranges[i]
            seg_dur = max(end - start, 0.05)
            delay_ms = max(0, int(start * 1000))
            filter_parts.append(
                f"[1:a]aloop=loop=-1:size=200000000,atrim=0:{seg_dur:.3f},"
                f"aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,"
                f"adelay={delay_ms}:all=1[beep{i}]"
            )
            mix_inputs.append(f"[beep{i}]")
        n = len(mix_inputs)
        filter_parts.append(
            f"{''.join(mix_inputs)}amix=inputs={n}:duration=first:dropout_transition=0:normalize=0[aout]"
        )
        filter_complex = ";".join(filter_parts)
 
        cmd = [
            get_ffmpeg(),
            "-i", media,
            "-i", beep_file,
            "-filter_complex", filter_complex,
            "-map", "0:v?",
            "-map", "[aout]",
            "-c:v", "copy",
            "-y",
            output_file
        ]
    else:
        # Plain mute, same as before
        cmd = [
            get_ffmpeg(),
            "-i", media,
            "-af", volume_filter,
            "-c:v", "copy",    # don't touch video
            "-y",              # overwrite output
            output_file
        ]
 
    subprocess.run(cmd, check=True)
    print(f"✅ Censored video saved as: {output_file}")
 
    # Replace original file
    os.remove(media)
    os.rename(output_file, media)
 
    return {"path": media, "mute_ranges": mute_ranges, "duration": duration}


#censor_video(input("Enter the path of the video to censor: ").strip())