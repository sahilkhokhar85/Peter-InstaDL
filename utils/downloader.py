"""
Instagram & Media Downloader via yt-dlp.
Handles Instagram Reels, Posts, IGTV, Stories, and HLS streams.
"""
import os
import re
import asyncio
import time
import logging
import config

logger = logging.getLogger("instagram_downloader")

UPDATE_SEC = 4

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

GLOBAL_COOKIE_FILE = os.getenv("COOKIES_FILE", "/tmp/cookies.txt")

_COOKIE_SITES = re.compile(
    r"(instagram\.com)",
    re.I,
)

_IMPERSONATE_TARGETS = [
    ("chrome", "131", "windows"),
    ("chrome", "124", "windows"),
    ("chrome", None,  None),
]

_CACHED_IMPERSONATE_TARGET = None
_IMPERSONATE_CHECKED = False

_IMP_ERR_KW = (
    "impersonate",
    "curl_cffi",
    "unsupported",
    "no such target",
    "the page needs to be reloaded",
    "page needs to be reloaded",
    "reload",
    "not available",
)

_HLS_ERR_KW = (
    "fragment", "m3u8", "hls",
    "no video formats found",
    "requested format is not available",
    "there is no media",
    "unable to download",
    "403", "404",
)

_FMT_ERR_KW = (
    "no video formats found",
    "requested format is not available",
    "format is not available",
    "no formats found",
    "no suitable formats",
)


def _get_impersonate():
    """Find and validate available ImpersonateTarget for yt-dlp (cached)."""
    global _CACHED_IMPERSONATE_TARGET, _IMPERSONATE_CHECKED
    if _IMPERSONATE_CHECKED:
        return _CACHED_IMPERSONATE_TARGET

    _IMPERSONATE_CHECKED = True
    try:
        import yt_dlp
        from yt_dlp.networking.impersonate import ImpersonateTarget
        for client, version, os_ in _IMPERSONATE_TARGETS:
            try:
                target = ImpersonateTarget(client=client, version=version, os=os_)
                with yt_dlp.YoutubeDL({"impersonate": target, "quiet": True}) as _:
                    pass
                _CACHED_IMPERSONATE_TARGET = target
                logger.info(f"Validated impersonate target: {target}")
                return target
            except Exception:
                continue
    except ImportError:
        pass
    _CACHED_IMPERSONATE_TARGET = None
    return None


def extract_qualities_from_info(info: dict) -> list[str]:
    """Extract unique sorted video resolution strings (e.g. ['1080p', '720p', '480p']) from yt-dlp info dict."""
    if not info:
        return []

    formats = info.get("formats", [])
    if not formats and "entries" in info:
        for entry in info.get("entries", []):
            if entry and "formats" in entry:
                formats.extend(entry.get("formats", []))

    available_heights = set()
    for f in formats:
        vcodec = f.get("vcodec") or ""
        height = f.get("height")
        width = f.get("width")
        note = str(f.get("format_note") or "")

        if vcodec and vcodec.lower() != "none":
            res_val = None
            match = re.search(r"(\d{3,4})p", note, re.I)
            if match:
                res_val = int(match.group(1))
            elif height and isinstance(height, int) and height > 0:
                res_val = min(height, width) if (width and isinstance(width, int) and width > 0) else height

            if res_val and res_val > 0:
                available_heights.add(res_val)

    sorted_h = sorted(list(available_heights), reverse=True)
    return [f"{h}p" for h in sorted_h]


def _needs_cookies(url: str) -> bool:
    return bool(_COOKIE_SITES.search(url))


def _get_cookies_path(uid: int, url: str) -> str | None:
    """Return cookie file path if the site needs it."""
    if not _needs_cookies(url):
        return None

    if uid:
        try:
            from database import users_db
            s = users_db.get_settings(uid)
            cp = s.get("cookies_path")
            if cp and os.path.exists(cp):
                return cp
        except Exception:
            pass

    if GLOBAL_COOKIE_FILE and os.path.exists(GLOBAL_COOKIE_FILE):
        return GLOBAL_COOKIE_FILE

    return None


def _is_m3u8(url: str) -> bool:
    return bool(re.search(
        r"\.m3u8(\?|#|$)"
        r"|/hls/"
        r"|/m3u8/"
        r"|playlist\.m3u8"
        r"|index\.m3u8"
        r"|master\.m3u8"
        r"|chunklist\.m3u8"
        r"|[?&]format=(hls|m3u8)"
        r"|[?&]type=(hls|m3u8)",
        url, re.I,
    ))


def _format_selector_for_quality(target_q: str | None, is_hls: bool = False) -> str:
    """Build yt-dlp format selector string from target quality resolution (e.g. '720p', '1080p', 'best')."""
    if not target_q or target_q == "best":
        return "best/bestvideo+bestaudio" if is_hls else "bv*+ba/b"

    match = re.search(r"(\d{3,4})", target_q)
    if match:
        height = int(match.group(1))
        return f"bv*[height<={height}]+ba/b[height<={height}]/best[height<={height}]/best"
    return target_q


def _build_opts(cookies=None, impersonate=None, fmt_override=None, hls_native=False, outtmpl=None, hook=None, is_hls=False, fast_info=False) -> dict:
    if fmt_override:
        fmt = _format_selector_for_quality(fmt_override, is_hls)
    elif is_hls:
        fmt = "best/bestvideo+bestaudio"
    else:
        fmt = "bv*+ba/b"

    retries = 2 if fast_info else 3
    fragment_retries = 2 if fast_info else 3
    timeout = 10 if fast_info else 20

    opts: dict = {
        "format":                        fmt,
        "merge_output_format":           "mp4",
        "writethumbnail":                False,
        "writesubtitles":                False,
        "writeautomaticsub":             False,
        "writedescription":              False,
        "writeinfojson":                 False,
        "noprogress":                    True,
        "overwrites":                    True,
        "trim_file_name":                180,
        "fragment_retries":              fragment_retries,
        "retries":                       retries,
        "file_access_retries":           2,
        "extractor_retries":             2,
        "socket_timeout":                timeout,
        "quiet":                         True,
        "no_warnings":                   True,
        "noplaylist":                    not is_hls,
        "concurrent_fragment_downloads": 4,
        "geo_bypass":                    True,
        "nocheckcertificate":            True,
        "source_address":                "0.0.0.0",
        "keepvideo":                     False,
        "http_headers": {
            "User-Agent":         UA,
            "Accept-Language":    "en-US,en;q=0.9",
            "Accept":             "*/*",
            "Sec-Fetch-Mode":     "navigate",
            "Sec-Ch-Ua":          '"Chromium";v="131","Google Chrome";v="131"',
            "Sec-Ch-Ua-Mobile":   "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
        },
        "retry_sleep_functions": {
            "http":        lambda n: 1,
            "fragment":    lambda n: 1,
            "file_access": lambda n: 1,
            "extractor":   lambda n: 1,
        },
        "postprocessors": [
            {
                "key": "FFmpegVideoConvertor",
                "preferedformat": "mp4",
            },
            {
                "key": "FFmpegMetadata",
                "add_metadata": True,
                "add_chapters": True,
            },
        ],
    }

    if outtmpl:
        opts["outtmpl"] = {"default": outtmpl}

    if hook:
        opts["progress_hooks"] = [hook]

    if is_hls:
        opts["hls_use_mpegts"]          = True
        opts["hls_split_discontinuity"] = True
        if hls_native:
            opts["hls_prefer_native"] = True

    if cookies:
        opts["cookiefile"] = cookies

    if impersonate is not None:
        opts["impersonate"] = impersonate

    return opts


async def ytdlp_extract_info(url: str, uid: int = 0) -> dict:
    """Fetch metadata and extract available video/audio qualities for Instagram links."""
    import yt_dlp

    loop = asyncio.get_running_loop()
    cookies = _get_cookies_path(uid, url)
    is_hls = _is_m3u8(url)

    def _extract():
        imp = _get_impersonate()
        opts = _build_opts(cookies=cookies, impersonate=imp, is_hls=is_hls, fast_info=True)
        opts["skip_download"] = True

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception:
            opts_no_imp = _build_opts(cookies=cookies, impersonate=None, is_hls=is_hls, fast_info=True)
            opts_no_imp["skip_download"] = True
            with yt_dlp.YoutubeDL(opts_no_imp) as ydl:
                info = ydl.extract_info(url, download=False)

        if not info:
            raise RuntimeError("Could not extract Instagram media info")

        formats = info.get("formats", [])
        available_heights = set()
        has_audio = False

        for f in formats:
            vcodec = f.get("vcodec") or ""
            acodec = f.get("acodec") or ""
            height = f.get("height")
            width = f.get("width")
            note = str(f.get("format_note") or "")

            if vcodec and vcodec.lower() != "none":
                res_val = None
                match = re.search(r"(\d{3,4})p", note, re.I)
                if match:
                    res_val = int(match.group(1))
                elif height and isinstance(height, int) and height > 0:
                    res_val = min(height, width) if (width and isinstance(width, int) and width > 0) else height

                if res_val and res_val > 0:
                    available_heights.add(res_val)

            if acodec and acodec.lower() != "none":
                has_audio = True

        sorted_qualities = sorted(list(available_heights), reverse=True)

        return {
            "title": info.get("title") or "Instagram Media",
            "description": info.get("description") or info.get("title") or "",
            "duration": info.get("duration") or 0,
            "views": info.get("view_count") or 0,
            "thumbnail": info.get("thumbnail"),
            "uploader": info.get("uploader") or info.get("channel") or "Instagram",
            "available_qualities": sorted_qualities,
            "has_audio": has_audio,
            "url": url,
        }

    return await loop.run_in_executor(None, _extract)


async def ytdlp_download(
    url: str,
    dest_dir: str,
    task_id: str,
    msg,
    uid: int = 0,
    format_override: str | None = None,
) -> tuple[str, str, list[str]]:
    import yt_dlp
    from database import task_manager as tm
    from utils.progress import build_progress_card, safe_edit

    os.makedirs(dest_dir, exist_ok=True)
    loop     = asyncio.get_running_loop()
    out_path = [None]
    desc_ref = [""]
    qualities_ref = [[]]
    err_ref  = [None]
    last_upd = [0.0]
    started  = time.time()
    is_hls   = _is_m3u8(url)
    cookies  = _get_cookies_path(uid, url)

    yt_ver = getattr(yt_dlp.version, "__version__", "unknown")
    cookie_exists = os.path.exists(cookies) if cookies else False
    cookie_readable = os.access(cookies, os.R_OK) if cookie_exists else False
    cookie_base = os.path.basename(cookies) if cookies else "None"

    logger.info(
        f"[Download Task {task_id}] URL: {url[:60]}... | yt-dlp version: {yt_ver} | "
        f"Cookie configured: {'YES' if cookies else 'NO'} | "
        f"Cookie file exists: {'YES' if cookie_exists else 'NO'} | "
        f"Cookie file readable: {'YES' if cookie_readable else 'NO'} | "
        f"Cookie file basename: {cookie_base}"
    )

    outtmpl = os.path.join(dest_dir, "%(title,id).180B.%(ext)s")

    def _hook(d: dict):
        if tm.is_cancelled(task_id):
            raise yt_dlp.utils.DownloadCancelled()

        status = d.get("status", "")
        if status == "finished":
            fn = d.get("filepath") or d.get("filename")
            if fn:
                out_path[0] = fn

        elif status == "downloading":
            done  = d.get("downloaded_bytes") or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            speed = d.get("speed") or 0.0
            eta   = d.get("eta") or 0.0
            fname = os.path.basename(d.get("filename") or "") or "Downloading…"

            if is_hls and not total:
                frag_idx   = d.get("fragment_index") or 0
                frag_count = d.get("fragment_count") or 0
                if frag_count:
                    done  = frag_idx
                    total = frag_count

            tm.update_progress(task_id, name=fname, done=done, total=total,
                               speed=speed, eta=eta, status="downloading")

            now = time.monotonic()
            if now - last_upd[0] >= UPDATE_SEC:
                last_upd[0] = now
                pct = (done / total * 100) if total else 0
                asyncio.run_coroutine_threadsafe(
                    safe_edit(
                        msg,
                        build_progress_card(
                            "downloading", fname, pct,
                            done=done, total=total,
                            speed=speed, eta=eta,
                            elapsed=time.time() - started,
                            tid=task_id,
                            user_mention=tm.get_user_mention(task_id),
                        ),
                    ),
                    loop,
                )

    def _make_opts(impersonate=None, fmt_override=format_override, hls_native=False) -> dict:
        return _build_opts(
            cookies=cookies,
            impersonate=impersonate,
            fmt_override=fmt_override,
            hls_native=hls_native,
            outtmpl=outtmpl,
            hook=_hook,
            is_hls=is_hls,
        )

    ydl_ref = [None]

    def _resolve_path(info) -> str | None:
        if not info:
            return None
        if info.get("description"):
            desc_ref[0] = info.get("description")
        elif info.get("title"):
            desc_ref[0] = info.get("title")

        extracted_q = extract_qualities_from_info(info)
        if extracted_q:
            qualities_ref[0] = extracted_q

        for entry in (info.get("requested_downloads") or []):
            for key in ("filepath", "filename"):
                fn = entry.get(key, "")
                if fn and os.path.exists(fn):
                    return fn
        if ydl_ref[0]:
            try:
                fn = ydl_ref[0].prepare_filename(info)
                if fn and os.path.exists(fn):
                    return fn
                base = os.path.splitext(fn)[0]
                for ext in (".mp4", ".mkv", ".webm", ".m4a", ".mp3"):
                    candidate = base + ext
                    if os.path.exists(candidate):
                        return candidate
            except Exception:
                pass
        return out_path[0]

    def _run_once(opts: dict):
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl_ref[0] = ydl
            info = ydl.extract_info(url, download=True)
            if info is None:
                raise RuntimeError("yt-dlp returned no info — download may have failed silently")
            path = _resolve_path(info)
            if path:
                out_path[0] = path

    def _run():
        imp  = _get_impersonate()
        opts = _make_opts(imp)
        logger.info(f"[Task {task_id}] Attempt 1: Standard download (impersonation={'ENABLED (' + str(imp) + ')' if imp else 'DISABLED'})")
        try:
            _run_once(opts)
            return
        except yt_dlp.utils.DownloadCancelled:
            return
        except Exception as e:
            err1 = str(e).lower()

            if imp is not None and any(kw in err1 for kw in _IMP_ERR_KW):
                logger.info(f"[Task {task_id}] Attempt 2: Impersonation error encountered -> retrying without impersonation")
                try:
                    _run_once(_make_opts(None))
                    return
                except yt_dlp.utils.DownloadCancelled:
                    return
                except Exception as e2:
                    err_ref[0] = e2
                    err1 = str(e2).lower()

            if any(kw in err1 for kw in _FMT_ERR_KW):
                logger.info(f"[Task {task_id}] Attempt 3: Format error -> retrying with format=best")
                try:
                    _run_once(_make_opts(None, fmt_override="best"))
                    return
                except yt_dlp.utils.DownloadCancelled:
                    return
                except Exception as e3:
                    err_ref[0] = e3
                    err1 = str(e3).lower()

            if is_hls and any(kw in err1 for kw in _HLS_ERR_KW):
                logger.info(f"[Task {task_id}] Attempt 4: HLS error -> retrying with native HLS downloader")
                try:
                    _run_once(_make_opts(None, hls_native=True))
                    return
                except yt_dlp.utils.DownloadCancelled:
                    return
                except Exception as e4:
                    err_ref[0] = e4
                    return

            if not err_ref[0]:
                err_ref[0] = e

    tm.set_status(task_id, "downloading")
    await loop.run_in_executor(None, _run)

    if tm.is_cancelled(task_id):
        raise asyncio.CancelledError

    if err_ref[0]:
        raise RuntimeError(
            f"yt-dlp failed [{url[:80]}]: {err_ref[0]}"
        ) from err_ref[0]

    if not out_path[0] or not os.path.exists(out_path[0]):
        files = [
            os.path.join(dest_dir, f)
            for f in os.listdir(dest_dir)
            if os.path.isfile(os.path.join(dest_dir, f))
            and not f.endswith((".part", ".ytdl", ".tmp", ".json", ".description"))
        ]
        out_path[0] = max(files, key=os.path.getmtime) if files else None

    if not out_path[0] or not os.path.exists(out_path[0]):
        raise FileNotFoundError(f"yt-dlp produced no output in {dest_dir}")

    # Fallback quality inspection using ffprobe if no format heights found
    if not qualities_ref[0] and os.path.splitext(out_path[0])[1].lower() in {".mp4", ".mkv", ".webm", ".avi", ".mov"}:
        try:
            from utils.media_processor import get_video_dimensions
            w, h = get_video_dimensions(out_path[0])
            if w > 0 and h > 0:
                res_p = min(w, h)
                qualities_ref[0] = [f"{res_p}p"]
        except Exception:
            pass

    return out_path[0], desc_ref[0], qualities_ref[0]


def fetch_instagram_cover(url: str, out_path: str, uid: int = 0) -> str:
    """Download the real Instagram cover (thumbnail) image for a post/reel URL.

    Blocking function: call it via loop.run_in_executor(). Saves a JPEG at out_path.
    Raises RuntimeError if no cover could be fetched.
    """
    import urllib.request
    import yt_dlp

    cookies = _get_cookies_path(uid, url)
    opts = _build_opts(cookies=cookies, impersonate=None, fast_info=True)
    opts["skip_download"] = True

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if info and info.get("entries"):
        entries = [e for e in info["entries"] if e]
        info = entries[0] if entries else info
    if not info:
        raise RuntimeError("Could not read Instagram media info for cover.")

    cover_url = None
    thumbs = [t for t in (info.get("thumbnails") or []) if t.get("url")]
    if thumbs:
        thumbs.sort(key=lambda t: (t.get("width") or 0) * (t.get("height") or 0))
        cover_url = thumbs[-1]["url"]
    if not cover_url:
        cover_url = info.get("thumbnail")
    if not cover_url:
        raise RuntimeError("Instagram did not return a cover image URL.")

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    raw_path = out_path + ".raw"
    req = urllib.request.Request(cover_url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as resp, open(raw_path, "wb") as f:
        f.write(resp.read())

    if not os.path.exists(raw_path) or os.path.getsize(raw_path) == 0:
        raise RuntimeError("Downloaded cover image is empty.")

    # Normalise to JPEG (Telegram-friendly); fall back to raw bytes if ffmpeg fails.
    try:
        from utils.media_processor import run_ffmpeg
        run_ffmpeg(["-y", "-i", raw_path, out_path])
        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            raise RuntimeError("ffmpeg produced empty cover.")
        os.remove(raw_path)
    except Exception:
        os.replace(raw_path, out_path)

    return out_path
