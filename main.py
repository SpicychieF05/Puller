# PATH: main.py
import os
import re
import tempfile
import threading
import time
import uuid
import shutil
import zipfile
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.background import BackgroundTask
import yt_dlp

app = FastAPI(title="yt-dlp downloader")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "ytdlp_downloader"
DOWNLOAD_DIR.mkdir(exist_ok=True)

AUDIO_BITRATES = ("48", "128", "256", "320")
JOB_TTL_SECONDS = 10 * 60

# job_id -> job dict
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()

# Concurrency cap for all yt-dlp operations
DOWNLOAD_SEMAPHORE = threading.Semaphore(2)

class InfoRequest(BaseModel):
    url: str

class DownloadRequest(BaseModel):
    url: str
    mode: str
    quality: str
    filename: str = ""

class PlaylistItem(BaseModel):
    url: str
    title: str
    index: int

class ZipDownloadRequest(BaseModel):
    items: list[PlaylistItem]
    mode: str
    quality: str
    filename: str = ""

def base_opts():
    return {"quiet": True, "no_warnings": True, "noplaylist": True}

def safe_filename(name: str) -> str:
    name = re.sub(r'[\\/*?:"<>|]', "", name or "video")
    return name.strip()[:150] or "video"

def requested_filename_base(name: str) -> str:
    if not name:
        return ""
    base = re.sub(r'[\\/*?:"<>|]', "", Path(name).stem)
    return base.strip()[:150]

def initial_job_state() -> dict:
    return {
        "status": "starting",
        "filepath": None,
        "filename": None,
        "error": None,
        "eta": None,
        "completed_at": None,
        "cancel_event": threading.Event(),
        "is_zip": False,
        "progress_text": None,
        "temp_dir": None,
    }

def cleanup_stale_jobs() -> None:
    now = time.time()
    with JOBS_LOCK:
        stale_ids = []
        for job_id, job in JOBS.items():
            if job.get("status") not in ("finished", "error", "cancelled"):
                continue
            completed_at = job.get("completed_at")
            if completed_at and now - completed_at > JOB_TTL_SECONDS:
                stale_ids.append(job_id)

        for job_id in stale_ids:
            job = JOBS[job_id]
            filepath = job.get("filepath")
            if filepath:
                Path(filepath).unlink(missing_ok=True)
            temp_dir = job.get("temp_dir")
            if temp_dir and Path(temp_dir).exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
            JOBS.pop(job_id, None)

def build_download_opts(mode: str, quality: str, out_template: str) -> dict:
    opts = base_opts()
    opts["outtmpl"] = out_template

    if mode == "video":
        m = re.match(r"^(\d+)(_hdr)?$", quality)
        if not m:
            raise HTTPException(status_code=400, detail="Invalid quality value.")
        height, hdr = m.group(1), bool(m.group(2))
        if hdr:
            fmt = (f"bestvideo[height<=?{height}][dynamic_range!=SDR]+bestaudio/"
                   f"best[height<=?{height}]")
        else:
            fmt = f"bestvideo[height<=?{height}]+bestaudio/best[height<=?{height}]"
        opts["format"] = fmt
        opts["merge_output_format"] = "mp4"
    elif mode == "audio":
        if quality not in AUDIO_BITRATES:
            raise HTTPException(status_code=400, detail="Invalid audio bitrate.")
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": quality,
        }]
    else:
        raise HTTPException(status_code=400, detail="mode must be 'video' or 'audio'.")

    return opts

def perform_single_download(url: str, mode: str, quality: str, out_template: str, cancel_event: threading.Event, progress_cb=None):
    def progress_hook(d: dict) -> None:
        if cancel_event.is_set():
            raise yt_dlp.utils.DownloadError("Cancelled by user")
        if progress_cb:
            progress_cb(d)

    opts = build_download_opts(mode, quality, out_template)
    opts["progress_hooks"] = [progress_hook]
    
    with DOWNLOAD_SEMAPHORE:
        if cancel_event.is_set():
            raise yt_dlp.utils.DownloadError("Cancelled by user")
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filepath = Path(ydl.prepare_filename(info))
            filepath = filepath.with_suffix(".mp3" if mode == "audio" else ".mp4")
            return filepath, info

def run_download_job(job_id: str, req: DownloadRequest) -> None:
    job = JOBS.get(job_id)
    if not job: return
    
    def progress_cb(d: dict):
        if d.get("status") == "downloading":
            with JOBS_LOCK:
                job["eta"] = d.get("eta")

    custom_base = requested_filename_base(req.filename)
    suffix = f"_{custom_base}" if custom_base else ""
    out_template = str(DOWNLOAD_DIR / f"{job_id}{suffix}.%(ext)s")

    try:
        filepath, info = perform_single_download(req.url, req.mode, req.quality, out_template, job["cancel_event"], progress_cb)
    except yt_dlp.utils.DownloadError as e:
        with JOBS_LOCK:
            if job["cancel_event"].is_set():
                job.update({"status": "cancelled", "completed_at": time.time()})
            else:
                job.update({"status": "error", "error": str(e), "completed_at": time.time()})
        if job["cancel_event"].is_set():
            for p in DOWNLOAD_DIR.glob(f"{job_id}*"):
                p.unlink(missing_ok=True)
        return
    except Exception as e:
        with JOBS_LOCK:
            job.update({"status": "error", "error": str(e), "completed_at": time.time()})
        return

    if not filepath.exists():
        with JOBS_LOCK:
            job.update({"status": "error", "error": "File was not created.", "completed_at": time.time()})
        return

    download_name_base = requested_filename_base(req.filename) or safe_filename(info.get('title'))
    download_name = f"{download_name_base}{filepath.suffix}"
    
    with JOBS_LOCK:
        job.update({
            "status": "finished",
            "filepath": str(filepath),
            "filename": download_name,
            "completed_at": time.time(),
        })

def run_zip_job(job_id: str, req: ZipDownloadRequest) -> None:
    job = JOBS.get(job_id)
    if not job: return
    
    temp_dir = DOWNLOAD_DIR / f"zip_{job_id}"
    temp_dir.mkdir(exist_ok=True)
    with JOBS_LOCK:
        job["temp_dir"] = str(temp_dir)
        
    try:
        for i, item in enumerate(req.items):
            if job["cancel_event"].is_set():
                raise yt_dlp.utils.DownloadError("Cancelled by user")
                
            with JOBS_LOCK:
                job["progress_text"] = f"Downloading {i+1} of {len(req.items)} — {item.title}"
                job["eta"] = None
                
            def progress_cb(d: dict):
                if d.get("status") == "downloading":
                    with JOBS_LOCK:
                        job["eta"] = d.get("eta")

            prefix = f"{(item.index + 1):03d} - "
            item_base = safe_filename(item.title)
            out_template = str(temp_dir / f"{prefix}{item_base}.%(ext)s")
            
            perform_single_download(item.url, req.mode, req.quality, out_template, job["cancel_event"], progress_cb)
            
        if job["cancel_event"].is_set():
            raise yt_dlp.utils.DownloadError("Cancelled by user")
            
        with JOBS_LOCK:
            job["progress_text"] = "Creating zip file..."
            job["eta"] = None
            
        zip_filename = f"{requested_filename_base(req.filename) or 'Playlist'}.zip"
        zip_filepath = DOWNLOAD_DIR / f"{job_id}.zip"
        
        with zipfile.ZipFile(zip_filepath, 'w', zipfile.ZIP_DEFLATED) as zf:
            for p in temp_dir.iterdir():
                if p.is_file():
                    zf.write(p, p.name)
                    
        shutil.rmtree(temp_dir, ignore_errors=True)
        
        with JOBS_LOCK:
            job.update({
                "status": "finished",
                "filepath": str(zip_filepath),
                "filename": zip_filename,
                "temp_dir": None,
                "completed_at": time.time(),
            })
            
    except yt_dlp.utils.DownloadError as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        with JOBS_LOCK:
            if job["cancel_event"].is_set():
                job.update({"status": "cancelled", "completed_at": time.time()})
            else:
                job.update({"status": "error", "error": str(e), "completed_at": time.time()})
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        with JOBS_LOCK:
            job.update({"status": "error", "error": str(e), "completed_at": time.time()})

def cleanup_job_file(job_id: str, filepath_str: str) -> None:
    Path(filepath_str).unlink(missing_ok=True)
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job and job.get("temp_dir"):
            shutil.rmtree(job["temp_dir"], ignore_errors=True)
        JOBS.pop(job_id, None)

@app.post("/api/info")
def get_info(req: InfoRequest):
    # Phase 1: cheap flat probe to detect playlist vs single video
    try:
        flat_opts = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist"}
        with yt_dlp.YoutubeDL(flat_opts) as ydl:
            probe = ydl.extract_info(req.url, download=False)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read that URL: {e}")

    if probe is None:
        raise HTTPException(status_code=400, detail="Could not read that URL.")

    is_playlist = probe.get("_type") in ("playlist", "multi_video")

    if is_playlist:
        entries = []
        for i, entry in enumerate(probe.get("entries", [])):
            if entry:
                vid_id = entry.get("id")
                thumb = entry.get("thumbnail")
                if not thumb and entry.get("thumbnails"):
                    thumb = entry.get("thumbnails")[0].get("url")
                if not thumb and vid_id:
                    thumb = f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg"

                entries.append({
                    "id": vid_id,
                    "title": entry.get("title", "Unknown"),
                    "duration": entry.get("duration"),
                    "url": entry.get("url") or (f"https://www.youtube.com/watch?v={vid_id}" if vid_id else ""),
                    "thumbnail": thumb or "",
                    "index": i
                })

        video_qualities = [
            {"height": 2160, "hdr": False},
            {"height": 1440, "hdr": False},
            {"height": 1080, "hdr": False},
            {"height": 720, "hdr": False},
            {"height": 480, "hdr": False},
            {"height": 360, "hdr": False},
        ]

        return {
            "is_playlist": True,
            "title": probe.get("title"),
            "uploader": probe.get("uploader"),
            "entries": entries,
            "video_qualities": video_qualities,
            "audio_bitrates": list(AUDIO_BITRATES),
        }

    # Phase 2: full extraction for single videos to get real format list
    try:
        with yt_dlp.YoutubeDL(base_opts()) as ydl:
            info = ydl.extract_info(req.url, download=False)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read that video: {e}")

    if info is None:
        raise HTTPException(status_code=400, detail="Could not read that video.")

    seen = set()
    for f in info.get("formats", []):
        h = f.get("height")
        if not h or f.get("vcodec") in (None, "none"):
            continue
        is_hdr = (f.get("dynamic_range") or "").upper() not in ("", "SDR", "NONE")
        seen.add((h, is_hdr))

    video_qualities = [
        {"height": h, "hdr": hdr} for (h, hdr) in sorted(seen, key=lambda x: (x[0], x[1]))
    ]

    return {
        "is_playlist": False,
        "title": info.get("title"),
        "thumbnail": info.get("thumbnail"),
        "duration": info.get("duration"),
        "uploader": info.get("uploader"),
        "video_qualities": video_qualities,
        "audio_bitrates": list(AUDIO_BITRATES),
    }

@app.post("/api/download/start")
def start_download(req: DownloadRequest):
    cleanup_stale_jobs()
    build_download_opts(req.mode, req.quality, "validate-only")

    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[job_id] = initial_job_state()

    thread = threading.Thread(target=run_download_job, args=(job_id, req), daemon=True)
    thread.start()
    return {"job_id": job_id}

@app.post("/api/download/zip/start")
def start_zip_download(req: ZipDownloadRequest):
    cleanup_stale_jobs()
    build_download_opts(req.mode, req.quality, "validate-only")

    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        job_state = initial_job_state()
        job_state["is_zip"] = True
        JOBS[job_id] = job_state

    thread = threading.Thread(target=run_zip_job, args=(job_id, req), daemon=True)
    thread.start()
    return {"job_id": job_id}

@app.post("/api/download/cancel/{job_id}")
def cancel_download(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Unknown job.")
        job["cancel_event"].set()
    return {"status": "cancelling"}

@app.get("/api/download/status/{job_id}")
def download_status(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Unknown job id.")
        return {
            "status": job.get("status"),
            "eta": job.get("eta"),
            "progress_text": job.get("progress_text"),
        }

@app.get("/api/download/file/{job_id}")
def download_file(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Unknown job id.")
        if job.get("status") == "cancelled":
            raise HTTPException(status_code=410, detail="Download cancelled.")
        if job.get("status") != "finished":
            raise HTTPException(status_code=409, detail="Job is not finished yet.")
        filepath = job.get("filepath")
        filename = job.get("filename")

    if not filepath:
        raise HTTPException(status_code=500, detail="Missing file path for finished job.")

    file_path_obj = Path(filepath)
    if not file_path_obj.exists():
        with JOBS_LOCK:
            JOBS.pop(job_id, None)
        raise HTTPException(status_code=404, detail="Downloaded file is no longer available.")

    return FileResponse(
        path=str(file_path_obj),
        filename=filename or file_path_obj.name,
        media_type="application/octet-stream",
        background=BackgroundTask(cleanup_job_file, job_id, str(file_path_obj)),
    )

@app.post("/api/reset")
def reset_state():
    with JOBS_LOCK:
        for job_id, job in JOBS.items():
            filepath = job.get("filepath")
            if filepath:
                Path(filepath).unlink(missing_ok=True)
            temp_dir = job.get("temp_dir")
            if temp_dir and Path(temp_dir).exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
        JOBS.clear()
        
        for p in DOWNLOAD_DIR.iterdir():
            if p.is_file():
                p.unlink(missing_ok=True)
            elif p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
    return {"status": "ok"}

app.mount("/media", StaticFiles(directory=Path(__file__).parent / "media"), name="media")
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
