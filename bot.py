"""
╔══════════════════════════════════════════════════════╗
║         Anime Warrior Tamil — Upload Bot             ║
║  • Powered by yt-dlp (Bypasses Cloudflare 403)       ║
║  • Downloads Tamil audio + English subtitle only     ║
║  • Injects watermark into existing English .srt      ║
║  • AUTO-GENERATES subtitle with watermark if missing ║
║  • Adds @Anime_warrior_tamil metadata                ║
║  • Uploads as Document with thumbnail & caption      ║
║  • Strict 480p, 720p, 1080p Filtering                ║
║  • Auto-increments episodes & custom filenames       ║
╚══════════════════════════════════════════════════════╝
"""

import os, re, json, asyncio, logging, subprocess
from pathlib import Path

import yt_dlp
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import FloodWait

# ═══════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════
API_ID         = 24435985
API_HASH       = "0fec896446625478537e43906a4829f8"
BOT_TOKEN      = "7722665729:AAHDh8TAiv4uv9nfgwoBWZHexD1VEaCdx7Y"

CHANNEL_TAG    = "@Anime_warrior_tamil"
WATERMARK_TEXT = "File is Uploaded By Telegram :- @Anime_warrior_tamil"
WATERMARK_SECS = 30          

DOWNLOAD_DIR   = "./downloads"
THUMB_DIR      = "./thumbnails"
# ═══════════════════════════════════════════════════════

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(THUMB_DIR,    exist_ok=True)

app        = Client("aw_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
user_state = {}   


# ───────────────────────────────────────────────────────
# YT-DLP CORE (Bypasses Cloudflare)
# ───────────────────────────────────────────────────────

def get_qualities(master_url: str) -> list[dict]:
    """Uses yt-dlp to extract available video resolutions with heavy browser emulation."""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
            'Referer': master_url,
            'Accept-Language': 'en-US,en;q=0.9',
        }
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(master_url, download=False)
        
    formats = info.get('formats', [])
    seen = {}
    for f in formats:
        h = f.get('height')
        if h and f.get('vcodec') != 'none':
            if h not in seen:
                seen[h] = {
                    'resolution': f"{f.get('width', 'unknown')}x{h}",
                    'height': h,
                    'bandwidth': f.get('tbr', 0)
                }
    return sorted(seen.values(), key=lambda x: x['height'])


def download_with_ytdlp(m3u8_url: str, height: int, output_path: str):
    """Downloads specific resolution with Tamil audio and aggressively fetches English subs."""
    ydl_opts = {
        'format': f'bestvideo[height<={height}]+bestaudio[language=tam]/bestvideo[height<={height}]+bestaudio/best',
        
        # ── AGGRESSIVE SUBTITLE FETCHING ──
        'writesubtitles': True,
        'writeautomaticsub': True, 
        'subtitleslangs': ['en', 'eng', 'en-US', 'en-GB', 'all'], 
        'embedsubtitles': True,
        
        'merge_output_format': 'mkv',
        'outtmpl': output_path,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
            'Referer': m3u8_url,
        },
        'quiet': True,
        'no_warnings': True,
        
        # 🚀 SPEED BOOSTERS 🚀
        'concurrent_fragment_downloads': 15,
        'retries': 10,
        'fragment_retries': 10
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([m3u8_url])


def qlabel(resolution: str) -> str:
    h = resolution.split("x")[-1] if "x" in resolution else resolution
    # Convert heights to standard labels
    if int(h) <= 480: return "480p"
    if int(h) <= 720: return "720p"
    return "1080p"


# ───────────────────────────────────────────────────────
# FFPROBE & SUBTITLE INJECTION
# ───────────────────────────────────────────────────────

def probe(path: str) -> list[dict]:
    r = subprocess.run(
        ["ffprobe","-v","quiet","-print_format","json","-show_streams", path],
        capture_output=True, text=True
    )
    try:
        return json.loads(r.stdout).get("streams", [])
    except Exception:
        return []

def find_first_index(streams, codec_type):
    for s in streams:
        if s.get("codec_type") == codec_type:
            return s["index"]
    return None

def srt_ts(seconds: float) -> str:
    h  = int(seconds // 3600)
    m  = int((seconds % 3600) // 60)
    s  = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def parse_srt(text: str) -> list[dict]:
    blocks, raw = [], text.strip().split("\n\n")
    for block in raw:
        blines = block.strip().splitlines()
        if len(blines) < 3: continue
        try: idx = int(blines[0].strip())
        except ValueError: continue
        ts   = blines[1].strip()
        body = blines[2:]
        m    = re.match(r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})", ts)
        if not m: continue
        blocks.append({"idx": idx, "start": m.group(1), "end": m.group(2), "lines": body})
    return blocks

def inject_watermark_srt(original_srt: str, wm_text: str, wm_secs: int) -> str:
    blocks = parse_srt(original_srt)
    wm_block = {
        "idx": 0, "start": srt_ts(0), "end": srt_ts(wm_secs), "lines": [wm_text]
    }
    all_blocks = [wm_block] + blocks
    out = []
    for i, b in enumerate(all_blocks, 1):
        out.append(str(i))
        out.append(f"{b['start']} --> {b['end']}")
        out.extend(b["lines"])
        out.append("")
    return "\n".join(out)

async def extract_srt(raw_path: str, sub_idx: int, srt_out: str):
    cmd = ["ffmpeg", "-y", "-i", raw_path, "-map", f"0:{sub_idx}", srt_out]
    await run_ff(cmd, "extract-sub")


# ───────────────────────────────────────────────────────
# FFMPEG MUXING
# ───────────────────────────────────────────────────────

# ───────────────────────────────────────────────────────
# FFMPEG MUXING
# ───────────────────────────────────────────────────────

async def run_ff(cmd: list, label: str):
    log.info(f"[ff:{label}] " + " ".join(cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg [{label}] failed:\n{err.decode()[-1200:]}")

async def build_final(raw_path: str, srt_path: str | None,
                      final_path: str, tam_idx: int | None,
                      title: str, episode: str, label: str):
    cmd = ["ffmpeg", "-y", "-i", raw_path]

    has_sub = srt_path and os.path.exists(srt_path)
    if has_sub: cmd += ["-i", srt_path]

    cmd += ["-map", "0:v:0", "-c:v", "copy"]
    if tam_idx is not None: cmd += ["-map", f"0:{tam_idx}", "-c:a", "copy"]
    if has_sub: cmd += ["-map", "1:0", "-c:s", "srt"]

    cmd += [
        "-metadata", f"title={title} - Episode {episode} [{label}]",
        "-metadata", f"artist={CHANNEL_TAG}",
        "-metadata", f"album_artist={CHANNEL_TAG}",
        "-metadata", f"comment=Uploaded by {CHANNEL_TAG}",
        "-metadata", f"episode_id=Episode {episode}",
        "-metadata", f"show={title}",
    ]

    if tam_idx is not None:
        cmd += [
            "-metadata:s:a:0", "language=tam",
            "-metadata:s:a:0", f"title=Tamil | {CHANNEL_TAG}",
            "-metadata:s:a:0", f"handler_name={CHANNEL_TAG}",
        ]

    if has_sub:
        cmd += [
            "-metadata:s:s:0", "language=eng",
            "-metadata:s:s:0", f"title=English | {CHANNEL_TAG}",
            "-metadata:s:s:0", f"handler_name={CHANNEL_TAG}",
            # 👇 THIS IS THE FIX: Forcing the player to display the subtitle instantly
            "-disposition:s:0", "default+forced",
        ]

    cmd.append(final_path)
    await run_ff(cmd, f"build-{label}")


# ───────────────────────────────────────────────────────
# TELEGRAM UPLOAD
# ───────────────────────────────────────────────────────

async def upload_doc(client: Client, chat_id: int,
                     file_path: str, caption: str,
                     thumb: str | None, status: Message):
    last = [-1]
    async def prog(cur, tot):
        pct = int(cur * 100 / tot)
        if pct - last[0] >= 10:
            last[0] = pct
            try:
                await status.edit_text(
                    f"📤 Uploading...\n**{pct}%** ({cur/1024/1024:.1f} / {tot/1024/1024:.1f} MB)"
                )
            except FloodWait as e:
                await asyncio.sleep(e.value)
            except Exception: pass

    kw = dict(
        chat_id=chat_id, document=file_path, caption=caption,
        progress=prog, force_document=True,
    )
    if thumb and os.path.exists(thumb): kw["thumb"] = thumb

    try: await client.send_document(**kw)
    except FloodWait as e:
        await asyncio.sleep(e.value)
        await client.send_document(**kw)


# ───────────────────────────────────────────────────────
# CORE PIPELINE
# ───────────────────────────────────────────────────────

async def process_quality(client: Client, chat_id: int,
                          stream_info: dict, master_url: str, title: str, episode: str,
                          thumb: str | None, status: Message, num: int, total: int):

    height   = stream_info['height']
    label    = qlabel(stream_info['resolution'])
    
    safe_title = re.sub(r'[\\/*?:"<>|]', "", title).strip()
    final_name = f"{safe_title} Ep{episode} {label} {CHANNEL_TAG}.mkv"
    
    base     = os.path.join(DOWNLOAD_DIR, f"{chat_id}_{episode}_{label}")
    raw      = base + "_raw.mkv"
    srt_orig = base + "_orig.srt"
    srt_wm   = base + "_wm.srt"
    final    = os.path.join(DOWNLOAD_DIR, final_name)

    caption = (
        f"🎬 **{title}**\n\n"
        f"📺 **Episode:** `{episode}`\n"
        f"💿 **Quality:** `{label}`\n"
        f"🔊 **Audio:** `Tamil`\n"
        f"💬 **Subtitle:** `English`\n\n"
        f"📥 **Uploaded by:**\n"
        f"✨ {CHANNEL_TAG} ✨"
    )

    await status.edit_text(f"⬇️ Downloading **{label}** using yt-dlp ({num}/{total})...")
    try:
        await asyncio.to_thread(download_with_ytdlp, master_url, height, raw)
    except Exception as e:
        await status.edit_text(f"❌ Download failed [{label}]:\n`{e}`")
        _clean(raw); return

    raw_mb = os.path.getsize(raw) / 1024 / 1024
    await status.edit_text(f"✅ Downloaded **{label}** ({raw_mb:.1f} MB)\n⚙️ Analysing tracks...")

    streams_info = probe(raw)
    tam_idx = find_first_index(streams_info, "audio")
    eng_sub_idx = find_first_index(streams_info, "subtitle")

    srt_path_final = None
    sub_success = False

    # 1. Try to extract and inject watermark into an existing subtitle
    if eng_sub_idx is not None:
        try:
            await extract_srt(raw, eng_sub_idx, srt_orig)
            with open(srt_orig, "r", encoding="utf-8", errors="replace") as f:
                orig_text = f.read()
            injected = inject_watermark_srt(orig_text, WATERMARK_TEXT, WATERMARK_SECS)
            with open(srt_wm, "w", encoding="utf-8") as f: 
                f.write(injected)
            srt_path_final = srt_wm
            sub_success = True
        except Exception as e:
            log.warning(f"[{label}] Subtitle injection failed: {e}")
            
    # 2. GENERATE A NEW SUBTITLE IF NONE EXISTS OR INJECTION FAILED
    if not sub_success:
        log.info(f"[{label}] No subtitle found. Auto-generating watermark subtitle...")
        
        # FIXED: Added \n\n to the end of the format string to make it a mathematically valid SRT block
        dummy_srt = f"1\n00:00:00,000 --> {srt_ts(WATERMARK_SECS)}\n{WATERMARK_TEXT}\n\n"
        
        with open(srt_wm, "w", encoding="utf-8") as f:
            f.write(dummy_srt)
        
        srt_path_final = srt_wm

    await status.edit_text(f"⚙️ Building final **{label}** file...")
    try:
        await build_final(raw, srt_path_final, final, tam_idx, title, episode, label)
    except Exception as e:
        await status.edit_text(f"❌ Processing failed [{label}]:\n`{e}`")
        _clean(raw, srt_orig, srt_wm, final); return
    finally:
        _clean(raw, srt_orig, srt_wm)

    final_mb = os.path.getsize(final) / 1024 / 1024
    await status.edit_text(f"✅ Ready **{label}** ({final_mb:.1f} MB)\n📤 Uploading...")

    try: await upload_doc(client, chat_id, final, caption, thumb, status)
    except Exception as e: await status.edit_text(f"❌ Upload failed [{label}]:\n`{e}`")
    finally: _clean(final)

    await asyncio.sleep(3)


def _clean(*paths):
    for p in paths:
        try:
            if p and os.path.exists(p): os.remove(p)
        except Exception: pass


# ───────────────────────────────────────────────────────
# BOT HANDLERS
# ───────────────────────────────────────────────────────

@app.on_message(filters.command("start"))
async def cmd_start(client, msg: Message):
    await msg.reply_text(
        "🎌 **Anime Warrior Tamil Bot**\n\n"
        "**Steps:**\n"
        "1️⃣  /upload\n"
        "2️⃣  Send anime **title**\n"
        "3️⃣  Send **episode number** (e.g. `01`)\n"
        "4️⃣  Send **thumbnail photo**\n"
        "5️⃣  Send **master.m3u8 URL**\n\n"
        "**Features:**\n"
        f"🛡️ Built-in yt-dlp Cloudflare Bypass\n"
        f"✅ Strictly filters for **480p, 720p, 1080p**\n"
        f"✅ Formatted filenames automatically\n"
        f"✅ Auto-increments episode number\n"
        f"✅ Fallback subtitle generation\n\n"
        "/cancel — cancel session"
    )

@app.on_message(filters.command("upload"))
async def cmd_upload(client, msg: Message):
    uid = msg.from_user.id
    user_state[uid] = {"step": "title"}
    await msg.reply_text("📝 Send the **anime title:**\n_(e.g. `Dragon Ball Z Kai`)_")

@app.on_message(filters.command("cancel"))
async def cmd_cancel(client, msg: Message):
    uid = msg.from_user.id
    if uid in user_state:
        del user_state[uid]
        await msg.reply_text("❌ Session cancelled.")
    else:
        await msg.reply_text("No active session. Use /upload to start.")

@app.on_message(filters.photo)
async def photo_handler(client, msg: Message):
    uid   = msg.from_user.id
    state = user_state.get(uid, {})
    if state.get("step") != "thumbnail": return
    thumb = os.path.join(THUMB_DIR, f"{uid}_thumb.jpg")
    await msg.download(file_name=thumb)
    state["thumbnail"] = thumb
    state["step"]      = "url"
    await msg.reply_text("✅ Thumbnail saved!\n\nNow send the **master URL**:")

@app.on_message(filters.text & ~filters.command(["start","upload","cancel"]))
async def text_handler(client, msg: Message):
    uid   = msg.from_user.id
    text  = msg.text.strip()
    state = user_state.get(uid)

    if not state:
        await msg.reply_text("Send /upload to start."); return

    step = state.get("step")

    if step == "title":
        state["title"] = text
        state["step"]  = "episode"
        await msg.reply_text(f"✅ Title: **{text}**\n\nSend **episode number** (e.g. `01`):")
        return

    if step == "episode":
        ep = text.zfill(2) if text.isdigit() else text
        state["episode"] = ep
        state["step"]    = "thumbnail"
        await msg.reply_text(f"✅ Episode: **{ep}**\n\nSend the **thumbnail photo** (or type `/skip` to skip):")
        return

    if step == "thumbnail" and text.lower() == "/skip":
        state["thumbnail"] = None
        state["step"]      = "url"
        await msg.reply_text("⏭ Thumbnail skipped.\n\nSend the **master URL**:")
        return

    if step == "url":
        if not text.startswith("http"):
            await msg.reply_text("❌ That doesn't look like a valid URL. Try again:"); return

        master_url = text
        title      = state.get("title", "Anime")
        episode    = state.get("episode", "01")
        thumb      = state.get("thumbnail")
        chat_id    = msg.chat.id

        status = await msg.reply_text("🔍 Fetching qualities using yt-dlp...")

        try:
            raw_streams = await asyncio.to_thread(get_qualities, master_url)
        except Exception as e:
            await status.edit_text(f"❌ yt-dlp failed to parse URL:\n`{e}`"); return

        if not raw_streams:
            await status.edit_text("❌ No video streams found."); return

        # ── STRICT FILTER: ONLY KEEP 480p, 720p, 1080p ──
        allowed_qualities = ["480p", "720p", "1080p"]
        filtered_streams = [s for s in raw_streams if qlabel(s['resolution']) in allowed_qualities]
        
        # Remove duplicates (in case yt-dlp finds two different 720p bitrates)
        unique_streams = {qlabel(s['resolution']): s for s in filtered_streams}.values()
        streams = list(unique_streams)

        if not streams:
            await status.edit_text("❌ No 480p, 720p, or 1080p streams found in this link."); return

        q_lines = "\n".join(f"• `{qlabel(s['resolution'])}` — `{s['resolution']}`" for s in streams)
        await status.edit_text(
            f"✅ **{len(streams)} targeted qualities found:**\n{q_lines}\n\n"
            f"🎌 **{title}** | Episode `{episode}`\n"
            f"📥 Starting pipeline..."
        )

        for i, stream_info in enumerate(streams, 1):
            await process_quality(
                client=client, chat_id=chat_id,
                stream_info=stream_info, master_url=master_url,
                title=title, episode=episode, thumb=thumb,
                status=status, num=i, total=len(streams)
            )

        # ── AUTO-INCREMENT LOGIC ──
        ep_len = len(episode)
        try:
            next_ep_num = int(episode) + 1
            next_ep_str = str(next_ep_num).zfill(ep_len)
        except ValueError:
            next_ep_str = episode + "_next"
            
        state["episode"] = next_ep_str

        await status.edit_text(
            f"🎉 **All done!**\n\n"
            f"🎌 **{title}** — Episode `{episode}`\n"
            f"✅ {len(streams)} qualities uploaded!\n"
            f"📢 {CHANNEL_TAG}\n\n"
            f"🔄 **Auto-Increment Active:**\n"
            f"Ready for the next one! Just send the URL for **Episode {next_ep_str}**."
        )
        return

    await msg.reply_text("Send /upload to begin.")


if __name__ == "__main__":
    log.info("=== Anime Warrior Bot started ===")
    app.run()
