"""
╔══════════════════════════════════════════════════════╗
║         Anime Warrior Tamil — Upload Bot             ║
║  • Downloads Tamil audio + English subtitle only     ║
║  • Injects watermark into existing English .srt      ║
║  • Adds @Anime_warrior_tamil metadata                ║
║  • Uploads as Document with thumbnail & caption      ║
║  • Auto-increments episodes & custom filenames       ║
╚══════════════════════════════════════════════════════╝
"""

import os, re, json, asyncio, logging, subprocess, urllib.request
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import FloodWait

# ═══════════════════════════════════════════════════════
#  CONFIG  — edit these
# ═══════════════════════════════════════════════════════
API_ID         = 24435985
API_HASH       = " 0fec896446625478537e43906a4829f8"
BOT_TOKEN      = "7722665729:AAHDh8TAiv4uv9nfgwoBWZHexD1VEaCdx7Y"

CHANNEL_TAG    = "@Anime_warrior_tamil"
WATERMARK_TEXT = "File is Uploaded By Telegram :- @Anime_warrior_tamil"
WATERMARK_SECS = 30          # seconds to show watermark in subtitle

DOWNLOAD_DIR   = "./downloads"
THUMB_DIR      = "./thumbnails"
# ═══════════════════════════════════════════════════════

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(THUMB_DIR,    exist_ok=True)

app        = Client("aw_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
user_state = {}   # {user_id: {step, title, episode, thumbnail}}


# ───────────────────────────────────────────────────────
# M3U8 PARSING
# ───────────────────────────────────────────────────────

def is_m3u8(text: str) -> bool:
    return bool(re.match(r"https?://.+\.m3u8", text.strip()))


def parse_master(master_url: str) -> list[dict]:
    """Return list of {bandwidth, resolution, uri} sorted low→high."""
    with urllib.request.urlopen(master_url) as r:
        content = r.read().decode("utf-8")

    base   = master_url.rsplit("/", 1)[0]
    lines  = content.splitlines()
    seen   = {}
    i      = 0

    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#EXT-X-STREAM-INF"):
            bw  = int(m.group(1)) if (m := re.search(r"BANDWIDTH=(\d+)", line)) else 0
            res = m.group(1) if (m := re.search(r"RESOLUTION=(\d+x\d+)", line)) else "unknown"
            i  += 1
            while i < len(lines) and lines[i].strip().startswith("#"):
                i += 1
            if i < len(lines):
                uri = lines[i].strip()
                if not uri.startswith("http"):
                    uri = base + "/" + uri
                seen[res] = {"bandwidth": bw, "resolution": res, "uri": uri}
        i += 1

    return sorted(seen.values(), key=lambda x: x["bandwidth"])


def qlabel(resolution: str) -> str:
    h = resolution.split("x")[-1] if "x" in resolution else resolution
    return {"270":"480p","360":"480p","480":"480p",
            "540":"720p","720":"720p","1080":"1080p"}.get(h, f"{h}p")


# ───────────────────────────────────────────────────────
# FFPROBE
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


def find_index(streams, codec_type, lang_hint):
    """Return stream index matching codec_type and language hint."""
    for s in streams:
        if s.get("codec_type") != codec_type:
            continue
        lang = s.get("tags", {}).get("language", "").lower()
        if lang_hint.lower() in lang:
            return s["index"]
    return None


def find_first_index(streams, codec_type):
    for s in streams:
        if s.get("codec_type") == codec_type:
            return s["index"]
    return None


# ───────────────────────────────────────────────────────
# SUBTITLE  — extract → inject watermark → re-import
# ───────────────────────────────────────────────────────

def srt_ts(seconds: float) -> str:
    h  = int(seconds // 3600)
    m  = int((seconds % 3600) // 60)
    s  = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_srt(text: str) -> list[dict]:
    """Parse SRT text into list of {idx, start, end, lines}."""
    blocks, raw = [], text.strip().split("\n\n")
    for block in raw:
        blines = block.strip().splitlines()
        if len(blines) < 3:
            continue
        try:
            idx  = int(blines[0].strip())
        except ValueError:
            continue
        ts   = blines[1].strip()
        body = blines[2:]
        m    = re.match(
            r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})", ts
        )
        if not m:
            continue
        blocks.append({"idx": idx, "start": m.group(1),
                       "end": m.group(2), "lines": body})
    return blocks


def ts_to_sec(ts: str) -> float:
    h, m, rest = ts.split(":")
    s, ms = rest.split(",")
    return int(h)*3600 + int(m)*60 + int(s) + int(ms)/1000


def inject_watermark_srt(original_srt: str, wm_text: str, wm_secs: int) -> str:
    """
    Prepend watermark entry for 0 → wm_secs.
    All original entries are kept as-is (they may overlap — players show both).
    Re-numbers all entries.
    """
    blocks = parse_srt(original_srt)

    wm_block = {
        "idx":   0,
        "start": srt_ts(0),
        "end":   srt_ts(wm_secs),
        "lines": [wm_text]
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
    """Extract subtitle track at sub_idx to .srt file."""
    cmd = [
        "ffmpeg", "-y",
        "-i", raw_path,
        "-map", f"0:{sub_idx}",
        srt_out
    ]
    await run_ff(cmd, "extract-sub")


# ───────────────────────────────────────────────────────
# FFMPEG HELPERS
# ───────────────────────────────────────────────────────

async def run_ff(cmd: list, label: str):
    log.info(f"[ff:{label}] " + " ".join(cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg [{label}] failed:\n{err.decode()[-1200:]}")


async def download_raw(uri: str, out: str, label: str, a_idx: int | None, s_idx: int | None):
    """Download HLS stream mapping ONLY specific requested audio/sub tracks to save bandwidth."""
    cmd = ["ffmpeg", "-y", "-i", uri, "-map", "0:v:0"] # Always grab the first video
    
    if a_idx is not None:
        cmd.extend(["-map", f"0:{a_idx}"])
    else:
        cmd.extend(["-map", "0:a:0?"]) # Ultimate fallback to grab first available audio

    if s_idx is not None:
        cmd.extend(["-map", f"0:{s_idx}"])
        
    cmd.extend(["-c", "copy", "-bsf:a", "aac_adtstoasc", out])
    await run_ff(cmd, f"dl-{label}")


async def build_final(raw_path: str, srt_path: str | None,
                      final_path: str, tam_idx: int,
                      title: str, episode: str, label: str):
    """
    Mux: video (copy) + Tamil audio (copy) + optional watermarked English SRT
    + full metadata. NO re-encoding of video or audio.
    """
    cmd = [
        "ffmpeg", "-y",
        "-i", raw_path,
    ]

    has_sub = srt_path and os.path.exists(srt_path)
    if has_sub:
        cmd += ["-i", srt_path]

    # Map video
    cmd += ["-map", "0:v:0", "-c:v", "copy"]

    # Map Tamil audio
    cmd += ["-map", f"0:{tam_idx}", "-c:a", "copy"]

    # Map subtitle from second input if available
    if has_sub:
        cmd += ["-map", "1:0", "-c:s", "mov_text"]

    # Global metadata
    cmd += [
        "-metadata", f"title={title} - Episode {episode} [{label}]",
        "-metadata", f"artist={CHANNEL_TAG}",
        "-metadata", f"album_artist={CHANNEL_TAG}",
        "-metadata", f"comment=Uploaded by {CHANNEL_TAG}",
        "-metadata", f"episode_id=Episode {episode}",
        "-metadata", f"show={title}",
    ]

    # Audio stream metadata
    cmd += [
        "-metadata:s:a:0", "language=tam",
        "-metadata:s:a:0", f"title=Tamil | {CHANNEL_TAG}",
        "-metadata:s:a:0", f"handler_name={CHANNEL_TAG}",
    ]

    # Subtitle stream metadata
    if has_sub:
        cmd += [
            "-metadata:s:s:0", "language=eng",
            "-metadata:s:s:0", f"title=English | {CHANNEL_TAG}",
            "-metadata:s:s:0", f"handler_name={CHANNEL_TAG}",
            "-disposition:s:0", "default",   # auto-show subtitle
        ]

    cmd.append(final_path)
    await run_ff(cmd, f"build-{label}")


# ───────────────────────────────────────────────────────
# UPLOAD  (as Document so player shows subtitle picker)
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
                    f"📤 Uploading...\n**{pct}%** "
                    f"({cur/1024/1024:.1f} / {tot/1024/1024:.1f} MB)"
                )
            except FloodWait as e:
                await asyncio.sleep(e.value)
            except Exception:
                pass

    kw = dict(
        chat_id=chat_id,
        document=file_path,
        caption=caption,
        progress=prog,
        force_document=True,
    )
    if thumb and os.path.exists(thumb):
        kw["thumb"] = thumb

    try:
        await client.send_document(**kw)
    except FloodWait as e:
        await asyncio.sleep(e.value)
        await client.send_document(**kw)


# ───────────────────────────────────────────────────────
# PIPELINE  — per quality
# ───────────────────────────────────────────────────────

async def process_quality(client: Client, chat_id: int,
                          stream: dict, title: str, episode: str,
                          thumb: str | None, status: Message, num: int, total: int):

    label    = qlabel(stream["resolution"])
    
    # Generate exactly formatted filename
    safe_title = re.sub(r'[\\/*?:"<>|]', "", title).strip() # Clean invalid file characters
    final_name = f"{safe_title} Ep{episode} {label} {CHANNEL_TAG}.mp4"
    
    base     = os.path.join(DOWNLOAD_DIR, f"{chat_id}_{episode}_{label}")
    raw      = base + "_raw.mp4"
    srt_orig = base + "_orig.srt"
    srt_wm   = base + "_wm.srt"
    final    = os.path.join(DOWNLOAD_DIR, final_name) # Final path uses formatted name

    caption = (
        f"🎌 **{title}**\n"
        f"📺 Episode `{episode}` | 🎞 `{label}`\n"
        f"🔊 Audio: `Tamil` | 💬 Sub: `English`\n"
        f"📢 {CHANNEL_TAG}"
    )

    # ── 1. PROBE M3U8 URL FOR TRACKS (Bandwidth Saver) ───
    await status.edit_text(f"🔍 Probing URL streams to select Tamil & English (Saving Bandwidth)...")
    url_streams = probe(stream["uri"])
    
    orig_tam = find_index(url_streams, "audio", "tam") or find_index(url_streams, "audio", "mal") or find_first_index(url_streams, "audio")
    orig_sub = find_index(url_streams, "subtitle", "eng") or find_first_index(url_streams, "subtitle")

    # ── 2. SELECTIVE DOWNLOAD ────────────────────────────
    await status.edit_text(
        f"⬇️ Downloading **{label}** ({num}/{total})\n"
        f"`{stream['resolution']}` — {stream['bandwidth']//1000} kbps\n"
        f"*(Only selected tracks mapping...)*"
    )
    try:
        await download_raw(stream["uri"], raw, label, orig_tam, orig_sub)
    except Exception as e:
        await status.edit_text(f"❌ Download failed [{label}]:\n`{e}`")
        _clean(raw); return

    raw_mb = os.path.getsize(raw) / 1024 / 1024
    await status.edit_text(
        f"✅ Downloaded **{label}** ({raw_mb:.1f} MB)\n"
        f"⚙️ Analysing local tracks..."
    )

    # ── 3. PROBE LOCAL TRACKS ────────────────────────────
    # Tracks will have new indices (e.g. 0,1,2) since we selectively downloaded them
    streams_info = probe(raw)

    tam_idx = find_index(streams_info, "audio", "tam") or find_first_index(streams_info, "audio")
    eng_sub_idx = find_index(streams_info, "subtitle", "eng") or find_first_index(streams_info, "subtitle")

    audio_langs = [f"{s.get('tags',{}).get('language','?')}({s['index']})" for s in streams_info if s.get("codec_type") == "audio"]
    sub_langs   = [f"{s.get('tags',{}).get('language','?')}({s['index']})" for s in streams_info if s.get("codec_type") == "subtitle"]
    
    await status.edit_text(
        f"🔍 Final Tracks mapped:\n"
        f"🔊 Audio: `{'  '.join(audio_langs)}`\n"
        f"💬 Subs:  `{'  '.join(sub_langs)}`\n"
        f"✅ Using Audio[{tam_idx}] + Sub[{eng_sub_idx}]\n"
        f"⚙️ Injecting watermark into subtitle..."
    )

    # ── 4. EXTRACT + INJECT WATERMARK IN SUBTITLE ────────
    srt_path_final = None
    if eng_sub_idx is not None:
        try:
            await extract_srt(raw, eng_sub_idx, srt_orig)
            with open(srt_orig, "r", encoding="utf-8", errors="replace") as f:
                orig_text = f.read()

            injected = inject_watermark_srt(orig_text, WATERMARK_TEXT, WATERMARK_SECS)

            with open(srt_wm, "w", encoding="utf-8") as f:
                f.write(injected)

            srt_path_final = srt_wm
            log.info(f"[{label}] Watermark injected into subtitle.")
        except Exception as e:
            log.warning(f"[{label}] Subtitle injection failed: {e} — skipping subtitle")
            srt_path_final = None

    # ── 5. BUILD FINAL ────────────────────────────────────
    await status.edit_text(
        f"⚙️ Building final **{label}** file\n"
        f"(Tamil audio + English sub + metadata — no re-encode)..."
    )
    try:
        await build_final(
            raw_path=raw, srt_path=srt_path_final,
            final_path=final, tam_idx=tam_idx,
            title=title, episode=episode, label=label
        )
    except Exception as e:
        await status.edit_text(f"❌ Processing failed [{label}]:\n`{e}`")
        _clean(raw, srt_orig, srt_wm, final); return
    finally:
        _clean(raw, srt_orig, srt_wm)

    final_mb = os.path.getsize(final) / 1024 / 1024
    await status.edit_text(
        f"✅ Ready **{label}** ({final_mb:.1f} MB)\n📤 Uploading as document..."
    )

    # ── 6. UPLOAD ─────────────────────────────────────────
    try:
        await upload_doc(client, chat_id, final, caption, thumb, status)
    except Exception as e:
        await status.edit_text(f"❌ Upload failed [{label}]:\n`{e}`")
    finally:
        _clean(final)

    await asyncio.sleep(3)


def _clean(*paths):
    for p in paths:
        try:
            if p and os.path.exists(p):
                os.remove(p)
        except Exception:
            pass


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
        "**Auto-magic features:**\n"
        f"✅ Maps Tamil audio + English sub ONLY (saves bandwidth)\n"
        f"✅ Formatted filenames automatically\n"
        f"✅ Auto-increments episode number after each run\n\n"
        "/cancel — cancel session"
    )


@app.on_message(filters.command("upload"))
async def cmd_upload(client, msg: Message):
    uid = msg.from_user.id
    user_state[uid] = {"step": "title"}
    await msg.reply_text("📝 Send the **anime title:**\n_(e.g. Dragon Ball Z Kai)_")


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
    if state.get("step") != "thumbnail":
        return
    thumb = os.path.join(THUMB_DIR, f"{uid}_thumb.jpg")
    await msg.download(file_name=thumb)
    state["thumbnail"] = thumb
    state["step"]      = "m3u8"
    await msg.reply_text(
        "✅ Thumbnail saved!\n\n"
        "Now send the **master.m3u8 URL**:"
    )


@app.on_message(filters.text & ~filters.command(["start","upload","cancel"]))
async def text_handler(client, msg: Message):
    uid   = msg.from_user.id
    text  = msg.text.strip()
    state = user_state.get(uid)

    if not state:
        await msg.reply_text("Send /upload to start."); return

    step = state.get("step")

    # ── title ──
    if step == "title":
        state["title"] = text
        state["step"]  = "episode"
        await msg.reply_text(
            f"✅ Title: **{text}**\n\n"
            "Send **episode number** (e.g. `01`):"
        )
        return

    # ── episode ──
    if step == "episode":
        ep = text.zfill(2) if text.isdigit() else text
        state["episode"] = ep
        state["step"]    = "thumbnail"
        await msg.reply_text(
            f"✅ Episode: **{ep}**\n\n"
            "Send the **thumbnail photo** (or type `/skip` to skip):"
        )
        return

    # ── allow skipping thumbnail ──
    if step == "thumbnail" and text.lower() == "/skip":
        state["thumbnail"] = None
        state["step"]      = "m3u8"
        await msg.reply_text("⏭ Thumbnail skipped.\n\nSend the **master.m3u8 URL**:")
        return

    # ── m3u8 ──
    if step == "m3u8":
        if not is_m3u8(text):
            await msg.reply_text("❌ That doesn't look like a valid `.m3u8` URL. Try again:"); return

        master_url = text
        title      = state.get("title", "Anime")
        episode    = state.get("episode", "01")
        thumb      = state.get("thumbnail")
        chat_id    = msg.chat.id
        
        # State is NOT deleted here anymore so we can auto-increment!

        status = await msg.reply_text("🔍 Fetching master playlist...")

        try:
            streams = parse_master(master_url)
        except Exception as e:
            await status.edit_text(f"❌ Cannot parse master.m3u8:\n`{e}`"); return

        if not streams:
            await status.edit_text("❌ No video streams found in master playlist."); return

        q_lines = "\n".join(
            f"• `{qlabel(s['resolution'])}` — `{s['resolution']}` — {s['bandwidth']//1000} kbps"
            for s in streams
        )
        await status.edit_text(
            f"✅ **{len(streams)} qualities found:**\n{q_lines}\n\n"
            f"🎌 **{title}** | Episode `{episode}`\n"
            f"📥 Starting pipeline..."
        )

        for i, stream in enumerate(streams, 1):
            await process_quality(
                client=client, chat_id=chat_id,
                stream=stream, title=title, episode=episode,
                thumb=thumb, status=status,
                num=i, total=len(streams)
            )

        # ── AUTO-INCREMENT LOGIC ──
        ep_len = len(episode)
        try:
            next_ep_num = int(episode) + 1
            next_ep_str = str(next_ep_num).zfill(ep_len) # Preserves leading zeros like '01' -> '02'
        except ValueError:
            next_ep_str = episode + "_next"
            
        state["episode"] = next_ep_str
        # Step remains "m3u8", thumb and title remain loaded

        await status.edit_text(
            f"🎉 **All done!**\n\n"
            f"🎌 **{title}** — Episode `{episode}`\n"
            f"✅ {len(streams)} qualities uploaded!\n"
            f"📢 {CHANNEL_TAG}\n\n"
            f"🔄 **Auto-Increment Active:**\n"
            f"Ready for the next one! Just send the `master.m3u8` URL for **Episode {next_ep_str}**."
        )
        return

    await msg.reply_text("Send /upload to begin.")


# ───────────────────────────────────────────────────────
if __name__ == "__main__":
    log.info("=== Anime Warrior Bot started ===")
    app.run()
