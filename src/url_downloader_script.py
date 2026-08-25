import yt_dlp
import os


def download_video(url):

 # Set basic download options
 options = {
    'outtmpl': 'uploads/%(title)s.%(ext)s',  # Save file as video title
    # require an actual video stream — without this, "best" can silently grab
    # a static image on sites where the linked post has no video (e.g. an
    # image-only tweet/post), which then fails later with no audio to transcribe
    "format": "bestvideo+bestaudio/best[vcodec!=none]"}

 # Create downloader
 with yt_dlp.YoutubeDL(options) as ydl:
    info = ydl.extract_info(url, download=False)
    if info.get("vcodec") in (None, "none") and not info.get("formats"):
        raise ValueError("This link doesn't appear to contain a video — it may be an image-only post.")
    ydl.download([url])

 filename = ydl.prepare_filename(ydl.extract_info(url, download=False))

 # Safety net: catch image files that slipped through despite the format filter
 if os.path.splitext(filename)[1].lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
    raise ValueError("The downloaded file is an image, not a video — this link has no video/audio to censor.")

 return filename


