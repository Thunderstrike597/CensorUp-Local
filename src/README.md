# 🤫CensorUp
> Open-source automatic audio & video profanity censoring web app

<img width="949" height="479" alt="Screenshot_4" src="https://github.com/user-attachments/assets/ed3af016-bb01-4b4d-896e-b2679e00c500" />


Short, privacy-focused tool to automatically censor specified words in uploaded or linked audio/video files. Built with FastHTML, Pocketbase and small helper scripts to download, process, and upload media.

## Features
- Fast, local processing of audio/video to mask or remove profane words
- Accepts file uploads or media URLs (YouTube, Facebook, Instagram, TikTok)
- Temporary storage in `/uploads` and automatic background deletion
- Simple web UI — no signup required

## Repository structure
- `main.py` — FastHTML web app and endpoints
- `censor_script.py` — media transcription/censoring logic
- `url_downloader_script.py` — downloads media from URLs
- `storage_script.py` — uploads censored file to pocketbase storage and removes it after while to save space
- `requirements.txt` — Python dependencies

## Quick start

1. Install dependencies

```bash
python -m pip install -r requirements.txt
```

2. Run the app

```bash
python main.py
```

3. Open your browser at http://localhost:5001 (or the address printed by the script)

**How to use**
- On the homepage you can either upload an audio/video file or paste a supported media URL.
- Enter a comma-separated list of words to censor (for example: `badword1,badword2`).
- Click the "Censor Now" button. The processing indicator will show while the app runs.
- After processing you will get a download link for the censored media.

## Contributing
Contributions are welcome. Please open issues or pull requests with improvements, bug fixes, or new features.

