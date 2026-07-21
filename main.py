import os
import re
import tempfile
import threading
import time
import uuid
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


class InfoRequest(BaseModel):
    url: str


class DownloadRequest(BaseModel):
    url: str
    mode: str      # "video" or "audio"
    quality: str   # e.g. "720", "1080_hdr", or "128"
    filename: str = ""


def base_opts():
    return {"quiet": True, "no_warnings": True, "noplaylist": True}


def safe_filename(name: str) -> str:
    name = re.sub(r'[\\/*?:"<>|]', "", name or "video")
    return name.strip()[:150] or "video"


def requested_filename_base(name: str) -> str:
    # Keep the caller-provided name, but normalize it to a safe base name.
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
            filepath = JOBS[job_id].get("filepath")
            if filepath:
                Path(filepath).unlink(missing_ok=True)
            JOBS.pop(job_id, None)


def build_download_opts(req: DownloadRequest, job_id: str) -> dict:
    custom_base = requested_filename_base(req.filename)
    suffix = f"_{custom_base}" if custom_base else ""
    out_template = str(DOWNLOAD_DIR / f"{job_id}{suffix}.%(ext)s")
    opts = base_opts()
    opts["outtmpl"] = out_template

    if req.mode == "video":
        m = re.match(r"^(\d+)(_hdr)?$", req.quality)
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
    elif req.mode == "audio":
        if req.quality not in AUDIO_BITRATES:
            raise HTTPException(status_code=400, detail="Invalid audio bitrate.")
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": req.quality,
        }]
    else:
        raise HTTPException(status_code=400, detail="mode must be 'video' or 'audio'.")

    return opts


def run_download_job(job_id: str, req: DownloadRequest) -> None:
    def progress_hook(d: dict) -> None:
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if job and job["cancel_event"].is_set():
                raise yt_dlp.utils.DownloadError("Cancelled by user")
            if d.get("status") == "downloading" and job:
                job["eta"] = d.get("eta")

    try:
        opts = build_download_opts(req, job_id)
        opts["progress_hooks"] = [progress_hook]
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(req.url, download=True)
            filepath = Path(ydl.prepare_filename(info))
            filepath = filepath.with_suffix(".mp3" if req.mode == "audio" else ".mp4")
    except yt_dlp.utils.DownloadError as e:
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if job:
                if job["cancel_event"].is_set():
                    job.update({
                        "status": "cancelled",
                        "error": None,
                        "completed_at": time.time(),
                    })
                else:
                    job.update({
                        "status": "error",
                        "error": str(e),
                        "completed_at": time.time(),
                    })
        if job and job["cancel_event"].is_set():
            for p in DOWNLOAD_DIR.glob(f"{job_id}*"):
                p.unlink(missing_ok=True)
        return
    except Exception as e:
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if job:
                job.update({
                    "status": "error",
                    "error": str(e),
                    "completed_at": time.time(),
                })
        return

    if not filepath.exists():
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if job:
                job.update({
                    "status": "error",
                    "error": "File was not created.",
                    "completed_at": time.time(),
                })
        return

    download_name_base = requested_filename_base(req.filename) or safe_filename(info.get('title'))
    download_name = f"{download_name_base}{filepath.suffix}"
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job:
            job.update({
                "status": "finished",
                "percent": 100,
                "filepath": str(filepath),
                "filename": download_name,
                "completed_at": time.time(),
            })


def cleanup_job_file(job_id: str, filepath_str: str) -> None:
    Path(filepath_str).unlink(missing_ok=True)
    with JOBS_LOCK:
        JOBS.pop(job_id, None)


@app.post("/api/info")
def get_info(req: InfoRequest):
    try:
        with yt_dlp.YoutubeDL(base_opts()) as ydl:
            info = ydl.extract_info(req.url, download=False)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read that video: {e}")

    if info.get("_type") == "playlist":
        raise HTTPException(
            status_code=400,
            detail="That looks like a playlist. Paste a single video URL instead.",
        )

    # Collect the (height, hdr) combinations that actually exist for this video.
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
    # Validate request early so bad requests fail before creating a worker thread.
    build_download_opts(req, "validate-only")

    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[job_id] = initial_job_state()

    thread = threading.Thread(target=run_download_job, args=(job_id, req), daemon=True)
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
        JOBS.clear()
        
        for p in DOWNLOAD_DIR.iterdir():
            if p.is_file():
                p.unlink(missing_ok=True)
    return {"status": "ok"}


# Serve the media files
app.mount("/media", StaticFiles(directory=Path(__file__).parent / "media"), name="media")

# Serve the frontend. Keep this mounted last so /api/* routes above take priority.
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
