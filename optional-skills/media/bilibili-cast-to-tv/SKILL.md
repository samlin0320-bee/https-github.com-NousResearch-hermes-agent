---
name: bilibili-cast-to-tv
description: "Cast Bilibili videos + live streams to LG webOS TV. Unified script: auto-detects live (m3u8 direct push ~2s) vs video (virtual MP4 proxy ~8-15s). Authenticated via bili-cli for 4K/1080高码率."
version: 1.0.0
author: Hermes Community
license: MIT
metadata:
  hermes:
    tags: [bilibili, casting, lgtv, ffmpeg, yt-dlp, streaming, media]
---

# Bilibili Video Casting to LG TV

Cast Bilibili (B站) videos to an LG webOS TV on the local network. Two modes:
1. **Seekable mode** (full download): yt-dlp full file → faststart remux → serve. Supports scrubbing/seeking, but startup grows with video length.
2. **Proxy mode (preferred for daily use)**: virtual MP4 proxy (`mp4_proxy_v3.py`) with on-demand CDN fetch. No full pre-download, full seek support, ~8-15s init on tested videos.

## Recommended defaults

- **Prefer proxy mode** (no full download) for faster startup.
- Quality priority: authenticated AVC `30112(1080高码率)` → `30080(1080)` → `30064(720)`. **Avoid 30120(4K) on older TVs** (pre-2020) — some 4K AVC streams use High@L5.1+ profiles that older webOS cannot decode.
- Authenticate via `bili login` credential → generate Netscape cookie file → pass to yt-dlp with `--cookies`.
- If proxy playback stutters, downgrade quality one step before switching to full-download mode.
- **Agent fallback**: if auto-selected format causes "this file cannot be recognized" on TV, retry with explicit `30080+30280` (safe 1080p AVC).

## Agent execution pattern

**No manual IP check needed** — `tv_discovery.py` auto-discovers the TV (probe → SSDP → TCP scan). If no TV is found, it raises a clear error.

```bash
# 1. Kill leftover processes on port
fuser -k 9234/tcp 2>/dev/null

# 2. Run in background (script never exits — HTTP server stays alive)
# Do NOT pass a TV IP — let auto-discovery find it
nohup python3 ~/.hermes/skills/media/bilibili-cast-to-tv/scripts/bili_cast.py "<url>" > /tmp/bili_cast.log 2>&1 &

# 3. Wait for output and check
sleep 18 && cat /tmp/bili_cast.log
# Look for "TV: {'payload': {'returnValue': True, ...}}" → success
# "⚠ TV at ... unreachable, discovering..." → auto-discovery kicked in
# "✅ Found TV at ..." → discovered new IP
# "❌ No LG TV found" → TV is off or not on WiFi
```

## Architecture

```
User sends Bilibili URL
         │
         ▼
┌─────────────────┐
│     yt-dlp      │  Extract video + audio M4S URLs
│  (with cookies) │  ~2 seconds
└────────┬────────┘
         │ video URL, audio URL (separate DASH streams)
         ▼
┌──────────────────────────────────┐
│          ffmpeg                   │
│  -headers "Referer: bilibili"    │  Adds required Referer header
│  -i VIDEO_M4S  -i AUDIO_M4S     │  Two separate inputs
│  -c copy (no transcoding)        │  CPU: <5%
│  -movflags frag_keyframe+        │  ← KEY: fragmented MP4
│           empty_moov             │  Playable from byte 0
│  -f mp4 output.mp4              │  Writes to local file
└────────┬─────────────────────────┘
         │ growing MP4 file on disk
         ▼
┌─────────────────────┐
│  Python HTTP Server  │  Serves growing file via chunked transfer
│  port 9234           │  Supports Range requests for seeking
│  Handles reconnects  │  (already-downloaded portion)
└────────┬────────────┘
         │ http://LAN_IP:9234/stream.mp4
         ▼
┌─────────────────────┐
│   LG TV via SSAP    │  ssap://com.webos.applicationManager/launch
│   WebSocket push     │  com.webos.app.mediadiscovery (native player)
└─────────────────────┘
```

## Prerequisites (IMPORTANT)

If running from WSL2, two Windows firewall rules are needed (run once in **admin PowerShell**):

```powershell
# 1. Allow HTTP server port (TV fetches video stream from WSL2)
netsh advfirewall firewall add rule name="WSL2 HTTP 9234" dir=in action=allow protocol=tcp localport=9234

# 2. Allow Hyper-V inbound (enables SSDP multicast TV discovery from WSL2)
Set-NetFirewallHyperVVMSetting -Name '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -DefaultInboundAction Allow
```

Without rule 1: TV cannot fetch the video stream → "device disconnected".
Without rule 2: SSDP discovery returns zero results → falls back to slower TCP port scan (still works, but ~3s slower).

## Scripts

Only two scripts — everything else was consolidated:

### `scripts/tv_discovery.py` — Shared TV discovery module

All TV discovery, connection, and push logic. Used by both `bili_cast.py` and `mp4_proxy_v3.py`.

**Discovery flow (3-tier, ~3s typical):**
1. **WSS 3001 probe** — try configured/provided IP first (instant)
2. **SSDP multicast** — `M-SEARCH` for `MediaRenderer:1`, filters LG/webOS responses (~3s)
3. **TCP 3001 subnet scan** — fallback if SSDP fails, 50 threads across /24 (~3s)

**Additional features:**
- **MAC-based matching** when multiple TVs found (reads MAC from `lgtv.yaml`)
- **Auto-updates `lgtv.yaml`** with new IP when TV DHCP changes
- **Copies TV auth key** to new IP entry in `lgtv_keys.json`

**WSL2 SSDP prerequisite:** Hyper-V firewall must allow inbound multicast. Run once in admin PowerShell:
```powershell
Set-NetFirewallHyperVVMSetting -Name '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -DefaultInboundAction Allow
```
Without this, SSDP sends but receives zero replies (WSL2 mirrored mode blocks inbound UDP multicast by default). TCP scan still works as fallback.

### `scripts/bili_cast.py` — Unified entry point (RECOMMENDED)

Auto-detects live streams vs videos. One script for everything.

```bash
python3 ~/.hermes/skills/media/bilibili-cast-to-tv/scripts/bili_cast.py <url> [tv_ip] [format] [workers]

# Video (auto-selects best AVC quality incl 4K if authenticated):
python3 bili_cast.py "https://b23.tv/xxxxx"
python3 bili_cast.py "https://b23.tv/xxxxx" <TV_IP> 30120+30280 20

# Live stream (auto-detected, m3u8 direct push):
python3 bili_cast.py "https://live.bilibili.com/103"
python3 bili_cast.py "https://b23.tv/V0VnBNa"
```

- **Video** → auto-selects best AVC format, delegates to `mp4_proxy_v3.py` (virtual proxy, ~8-15s init)
- **Live** → extracts m3u8 URL via yt-dlp `BiliLive` extractor, pushes directly to TV photovideo (~2s)
- Auto-reads `~/.bilibili-cli/credential.json` for authenticated cookies (4K/1080高码率)
- Default TV: auto-discovered on LAN (override with second argument)

### `scripts/mp4_proxy_v3.py` — Video proxy core

Virtual progressive MP4 proxy. Called by `bili_cast.py` for video content. Can also be used standalone.

### Cookie Management (critical for 4K / 1080P高码率)
- Preferred auth source: `bili login` → `~/.bilibili-cli/credential.json`
- For `yt-dlp`, convert credential into a **Netscape cookie file** and pass with `--cookies <file>`
- ⚠️ Do **not** rely on a manually concatenated `Cookie:` header only; this can falsely miss `4K(120)` / `1080P高码率(112)` even when account is annual VIP
- Practical pattern:
  1) `bili login` (once)
  2) build cookie file from `credential.json` (include at least `SESSDATA`, `bili_jct`, `DedeUserID`, `ac_time_value`)
  3) run `yt-dlp --cookies /tmp/bili.cookies.txt ...`
- Cookie file: prefer temp path (`/tmp/...`) and avoid committing into repositories

## LG C8 (webOS 4.5) Specific Findings (2026-03)

Extensive testing confirmed:
1. **NO AV1 support** — C8 (2018) cannot decode AV1. yt-dlp defaults to AV1. MUST force H.264 (`avc1`)
2. **HLS/m3u8 in photovideo — works for third-party CDN**: m3u8 URLs (e.g. from ikanbot) play fine directly in photovideo with MIME `application/vnd.apple.mpegurl`! B站 doesn't serve HLS so this doesn't help for B站, but for other sources just push the m3u8 URL directly — no proxy/browser needed.
3. **Browser HLS too slow** — hls.js loads but playback choppy/unusable on webOS 4.x
4. **NO TimeSeekRange.dlna.org** — only byte-range seek (OP=01). OP=10 breaks. Confirmed Gerbera#839
5. **Fragmented MP4 can't seek** — no moov index
6. **faststart MP4 = only seek method** — `ffmpeg -movflags +faststart`
7. **Plex uses native app + HLS/DASH** — not comparable to DLNA approach

### Recommended Cast Flow (C8): yt-dlp(H.264) → ffmpeg faststart → HTTP Range serve → photovideo push (~8-10s prep, full seek)

### Format Selection: yt-dlp defaults AV1. Force H.264: `yt-dlp -j <url>` → filter `vcodec.startswith('avc1')` → `-f <id>+<audio_id>`

## Why This Approach (Lessons Learned)

### Why not direct B站 DLNA casting (投屏)?
- B站 app's built-in 投屏 uses DLNA, but **LG webOS has poor DLNA compatibility with Bilibili** — devices often not found or playback fails
- Non-VIP users capped at **720p** via DLNA casting
- Cannot be triggered programmatically

### Why not stream CDN URLs directly to TV?
- Bilibili CDN requires **`Referer: https://www.bilibili.com/`** header — TV's built-in player cannot add custom headers → **403 Forbidden**
- Bilibili uses **DASH** (separate audio + video streams) — TV cannot merge them

### Why fragmented MP4?
- Standard MP4 has `moov` atom (index) at **end of file** — TV cannot play until file is fully downloaded
- **`-movflags frag_keyframe+empty_moov+default_base_moof`** puts metadata at the start and in each fragment → **playable from byte 0**
- Confirmed: ffprobe can read valid video data while ffmpeg is still writing

### Why not MPEGTS?
- LG webOS TVs have **known issues with MPEGTS** — black screen, erratic playback
- Fragmented MP4 works reliably on LG TVs

### Why not ffmpeg's built-in HTTP server?
- `-listen 1` only accepts **one client connection** — TV disconnects and reconnects during buffering → stream dies
- No Range request support (no seeking)
- Custom Python HTTP server handles reconnects and Range requests

## Performance (Tested)

- yt-dlp extraction: **~2 seconds**
- ffmpeg first data output: **~4 seconds** after start
- Total time to TV playback: **~6-8 seconds**
- CPU usage: **<5%** (remux only, no transcoding)
- Memory: **~50-100MB** (ffmpeg process)
- CDN URL validity: **~120 minutes** (deadline parameter in URL)

## Bilibili-Specific Notes

### URL Formats Supported
- `https://www.bilibili.com/video/BVxxxxxxxxxx` — standard videos
- `https://www.bilibili.com/video/BVxxxxxxxxxx/?p=2` — multi-part (分P) videos, select specific part
- `https://www.bilibili.com/video/avXXXXXXXX` — old format
- `https://www.bilibili.com/bangumi/play/epXXXXXX` — anime episodes
- `https://www.bilibili.com/bangumi/play/ssXXXXXX` — anime seasons
- `https://b23.tv/XXXXXXX` — short links (yt-dlp resolves these directly, no need to expand first)

### Multi-Part (分P) Videos
Many B站 videos are multi-part playlists. To detect and select parts:
1. **Check with `--flat-playlist -J`** to see if it's a playlist and how many parts
2. **b23.tv short links default to p=1** — resolve first to get the BV number
3. **Append `?p=N` to the URL** to select a specific part (1-indexed)
4. yt-dlp `-j` on a playlist URL without `?p=N` returns only p1's info

Example: User shares a link to "Our Planet 全8集" — it has p1=Trailer + p2-p9=episodes. To cast episode 1, use `?p=2`.

### Format Selection — CODEC MATTERS!

**CRITICAL: yt-dlp defaults to AV1 (smallest file) which FAILS on LG C8 and older TVs.**

LG C8 (webOS 4.5, 2018) does NOT support AV1. TV shows "This file type cannot be played on this TV".

**Always force H.264 for maximum compatibility:**
```bash
# List available formats:
yt-dlp -F "https://b23.tv/xxxxx"

# Force H.264 1080p + best audio:
yt-dlp -f "30080+30280" --merge-output-format mp4 ...

# For extract_streams, use: yt-dlp -j -f 30080+30280 and parse requested_formats
```

Bilibili format ID reference (authenticated):
- `30120` = H.264/AVC 4K (2160p) — preferred when available + account has access
- `30112` = H.264/AVC 1080P 高码率 (quality 112)
- `30080` = H.264/AVC 1080p (quality 80) — **SAFE fallback for all LG TVs**
- `30121` = HEVC/H.265 4K (2160p) — works on C8+, better compression
- `30077` = HEVC/H.265 1080p (~90MB)
- `100029`/`100026` = AV1 4K/1080p — **FAILS on C8 and pre-2020 TVs**
- `30064` = H.264 720p — fallback if 1080p unavailable

**Quick check:** If CDN URL contains `100026` or `av01`, it's AV1 — will fail on older TVs.

- Video streams are `.m4s` files
- Audio streams are `.m4s` files with AAC codec
- Do NOT use `-f bestvideo[ext=mp4]+bestaudio[ext=m4a]` — Bilibili streams are .m4s not .mp4

### Geo-Restrictions
- From US: many videos work, but some are geo-restricted to China
- Error: "This video may be deleted or geo-restricted"
- Workaround: VPN/proxy via `--proxy` flag

### CDN URL Expiry
- URLs contain `deadline=` parameter (Unix timestamp)
- Typically **2 hours** validity
- For videos longer than 2 hours, URL may expire mid-stream → ffmpeg gets 403 → stream dies
- Mitigation: re-extract URLs before expiry (not yet implemented)

### yt-dlp Maintenance
- Bilibili extractor (`yt_dlp/extractor/bilibili.py`) is actively maintained
- Bilibili periodically changes their API (WBI signing, etc.)
- Keep yt-dlp updated: `yt-dlp -U`

## SSAP Media Push Methods

### Method 1: `com.webos.app.mediadiscovery` (webOS 6+) / `com.webos.app.photovideo` (webOS 3-5)

**IMPORTANT:** The correct app ID depends on webOS version. Try in order:
1. `com.webos.app.photovideo` — webOS 3-5 (C8, C9, CX, etc.)
2. `com.webos.app.mediadiscovery` — webOS 6+ (C1, C2, C3, C4, C5, etc.)
3. `com.webos.app.smartshare` — webOS 1-2 (very old models)

Example: LG C8 = webOS 4.5 → use `com.webos.app.photovideo`.

Use `ssap://com.webos.applicationManager/launch` with the TV's native media player. **Works even when TV is in HDMI/PC input mode** (unlike `media.viewer/open`).

```python
payload = {
    "id": "com.webos.app.mediadiscovery",  # webOS 6+ (C1, C2, etc.)
    # Older: "com.webos.app.photovideo" (webOS 3-5), "com.webos.app.smartshare" (webOS 1-2)
    "params": {
        "payload": [{
            "fullPath": f"http://{LAN_IP}:{PORT}/video.mp4",
            "artist": "",
            "subtitle": "",
            "dlnaInfo": {
                "flagVal": 4096,
                "cleartextSize": "-1",
                "contentLength": "-1",
                "opVal": 1,
                "protocolInfo": "http-get:*:video/mp4:DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000",
                "duration": 988  # Video duration in seconds — set for correct progress bar!
            },
            "mediaType": "VIDEO",
            "thumbnail": "",
            "deviceType": "DMR",
            "album": "",
            "fileName": "Video Title Here",
            "lastPlayPosition": -1
        }]
    }
}

q = client.send_message('request', 'ssap://com.webos.applicationManager/launch', payload, get_queue=True)
resp = q.get(timeout=10)
```

### Method 2: `ssap://media.viewer/open` (BROKEN — do not use)

Returns `500 Application error` on LG C1, all input modes. Even switching to Home first doesn't fix it.

## Pitfalls

- **`ssap://media.viewer/open` returns 500 on both LG C1 and C8.** Do NOT use. Use `com.webos.app.photovideo` (webOS 3-5) or `com.webos.app.mediadiscovery` (webOS 6+).
- **WSL2 inbound connectivity blocked by Windows firewall.** Even with WSL2 bridged networking (real LAN IP), external devices **cannot reach HTTP servers** in WSL2. Fix: admin PowerShell `New-NetFirewallRule -DisplayName WSL2_HTTP -Direction Inbound -Protocol TCP -LocalPort 9234 -Action Allow`. **This was the root cause of all casting failures.**
- **Fragmented MP4 cannot seek/scrub on TV.** The TV player shows wrong duration and cannot fast-forward/rewind because fragmented MP4 (`-movflags frag_keyframe+empty_moov`) has no complete moov index. **For seekable playback, prefer the "full download + faststart" approach** (see below). Only use fragmented MP4 when "边下边播" (play while downloading) is needed and seeking is not important.
- **Preferred approach for seekable playback:** `yt-dlp -f bestvideo+bestaudio/best --merge-output-format mp4 -o output.mp4 URL` then `ffmpeg -i output.mp4 -c copy -movflags +faststart output_seekable.mp4`. The faststart flag moves moov atom to front → TV can seek properly. Trade-off: must wait for full download (~45s for a 13-min 95MB video at ~2MB/s from US).
- **HTTP server must stay alive for entire playback duration.** The server serving the video MUST NOT exit after pushing to TV — the TV streams from it the whole time. Use `time.sleep(duration + 60)` or run as background process. Killing the server = "device disconnected" on TV.
- **HTTP server should support HEAD requests.** Some TV players send HEAD before GET to check Content-Length. Add `do_HEAD` handler.
- **Set `contentLength` in dlnaInfo for seekable files.** When serving a complete file (not growing), set `contentLength` to the actual file size instead of "-1". This helps the TV player know the full file size for seeking.
- **LG C8 WOL (Wake on LAN) unreliable.** Sending WOL magic packet makes the NIC respond to ping, but webOS services (WSS on port 3001) do NOT start — TV screen stays off. User must manually power on with remote. To fix: TV Settings > General > Mobile TV On > "Turn on via Wi-Fi" must be enabled. Even then, WOL behavior varies by firmware. **Always confirm TV is actually awake (WSS connects) before attempting to push media.**
- **TV IP changes silently via DHCP — tv_discovery.py handles this automatically.** `resolve_tv_ip()` in `tv_discovery.py` (shared by both scripts): probes configured IP → tries SSDP multicast discovery → falls back to TCP 3001 subnet scan → MAC-matches when multiple TVs found → auto-updates `lgtv.yaml` and copies auth key. No manual IP hunting needed.
- **WSL2 multicast (SSDP) requires Hyper-V firewall allow rule.** Without `Set-NetFirewallHyperVVMSetting -Name '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -DefaultInboundAction Allow`, SSDP discovery returns zero results from WSL2 mirrored mode. Ping, ARP, and SSDP all fail; only TCP direct connect works. This is a WSL2-specific issue — the Hyper-V VMSwitch blocks inbound UDP multicast by default.
- **pywebostv keys are stored per-IP.** When TV IP changes, `tv_discovery.py` auto-copies the key to the new IP in `lgtv_keys.json`. If a fresh pairing is needed, TV shows a prompt on screen.
- **CLI script usage: `python bilibili_cast.py <url> [tv_ip]`** — do NOT pass a subcommand like `cast` as first arg, yt-dlp will try to parse it as a URL
- **Script blocks forever after cast** — the HTTP server runs in a daemon thread and the CLI `__main__` enters an infinite `while True: sleep(1)` loop. Use `timeout` or `head` to capture output, or run in background
- **`-f bestvideo[ext=mp4]` doesn't work for Bilibili** — streams are .m4s, not .mp4. Use `-f bestvideo+bestaudio/best`
- **MPEGTS format broken on LG TVs** — use fragmented MP4 only
- **ffmpeg `-headers` must end with `\r\n`** — `"Referer: https://www.bilibili.com/\r\nUser-Agent: Mozilla/5.0\r\n"`
- **ffmpeg `-headers` applies to the NEXT `-i` only** — must specify before each input separately
- **CDN URLs expire in ~2 hours** — long movies may fail
- **画质受认证链路影响**：匿名常被限到较低画质；即便是VIP，若只传手工 `Cookie:` 头也可能看不到 `4K(120)`/`1080P高码率(112)`。优先使用 `bili login` 后生成 Netscape cookies 文件并通过 `yt-dlp --cookies` 传入。
- **Port 9234** used for HTTP server — ensure not conflicting with other services. **Before starting a new cast, always `fuser -k 9234/tcp`** to kill any leftover process from a previous session, otherwise you get `OSError: Address already in use`.
- **HTTP server serves ANY GET as the video file** — no path matching (by design, simplicity)
- **Background process output buffering**: When running mp4_proxy_v3.py via non-TTY background mode, stdout may not appear even with `PYTHONUNBUFFERED=1`. The script IS working — verify by checking `ss -tlnp | grep 9234` (port listening = init complete + TV push done) and `ps aux` (CPU/memory usage = moof scanning in progress). Don't kill and retry just because output is empty.
- **C8 cannot play 4K AVC (30120)**: bili_cast.py auto-format picks highest AVC by resolution, which may select 30120 (4K). C8 shows "this file cannot be recognized" — likely High@L5.1+ profile unsupported. **Agent workaround**: when auto-format selects 30120 and TV fails, retry with `30080+30280`. Long-term fix: cap auto-format at 1080p for C8, or filter by codec profile/level.
- **Auto-format may select unavailable format IDs**: Not all videos have 30112 (1080P高码率) or 30120 (4K). If yt-dlp reports "Requested format is not available", fall back to `30080+30280` which is almost universally available.

## Alternative Approaches Considered

| Approach | Verdict |
|----------|---------|
| B站 app DLNA 投屏 | ❌ Poor LG webOS compatibility, 720p cap |
| TV built-in browser + HLS | 🔄 Browser launches via SSAP (`com.webos.app.browser` + `target` param). webOS 4.x supports HLS v5. Testing in progress (2026-03). |
| Bilibili TV app on LG | ❌ Not available on non-China LG TVs |
| Direct CDN URL to TV | ❌ 403 (no Referer header), DASH not merged |
| Screen mirroring/Miracast | ❌ High latency, quality loss |
| Android TV stick + B站 app | ✅ Best for daily use (Shield/Chromecast) |
| **This pipeline (yt-dlp+ffmpeg)** | ✅ Best programmatic solution, covers all content |

## HLS Streaming via Browser (Seekable without full download) — CONFIRMED WORKING

The default fragmented-MP4 pipeline doesn't support seeking. Full-download + faststart works but has ~8s startup. **HLS via browser gives both streaming AND seeking.**

### Architecture

```
Bilibili CDN → ffmpeg (single process, -f hls, remux) → .ts segments + .m3u8 on disk
                                                              ↓
Python HTTP server (port 9234) serves m3u8 + .ts + HTML player page
                                                              ↓
LG TV browser (com.webos.app.browser via SSAP) → HTML page with hls.js → plays video
```

### Key findings (tested 2026-03 on C8 / webOS 4.5):

1. **photovideo app DOES play third-party m3u8** (e.g. ikanbot CDN lines) with `protocolInfo` MIME `application/vnd.apple.mpegurl`. But B站 doesn't serve HLS — it uses DASH (separate audio+video M4S), so m3u8 is not applicable for B站.
2. **Browser + raw m3u8 URL** — loads but doesn't auto-play, shows static frame, user can seek but video stuck
3. **Browser + HTML page + native `<video>` + `<source type="application/x-mpegURL">`** — same problem, loads but doesn't play
4. **Browser + HTML page + hls.js library** — ✅ **WORKS!** Auto-plays, supports seeking
5. **Per-segment ffmpeg spawning** (ffmpeg -ss per segment on demand) — too slow, causes stuttering/buffering
6. **Single ffmpeg process generating HLS** (`ffmpeg -f hls -hls_time 4`) — ✅ fast, remux only, segments ready ahead of playback

### Working implementation: `scripts/hls_v2.py`

Single ffmpeg process remuxes DASH → HLS segments at near-realtime speed (faster than playback). hls.js in browser handles seeking within generated segments.

```bash
python /tmp/hls_v2.py  # after writing info.json with extract_streams output
```

**HTML page must include hls.js from CDN:**
```html
<script src="https://cdn.jsdelivr.net/npm/hls.js@1"></script>
<script>
var hls = new Hls({maxBufferLength: 60, maxMaxBufferLength: 120});
hls.loadSource('/stream.m3u8');
hls.attachMedia(videoElement);
hls.on(Hls.Events.MANIFEST_PARSED, function() { videoElement.play(); });
</script>
```

**Push to TV browser (NOT photovideo):**
```python
payload = {
    'id': 'com.webos.app.browser',
    'params': {'target': f'http://{LAN_IP}:{PORT}/'}
}
c.send_message('request', 'ssap://system.launcher/launch', payload, get_queue=True)
```

**ffmpeg HLS generation command:**
```bash
ffmpeg -headers "Referer: ...\r\n" -i VIDEO_URL -headers "..." -i AUDIO_URL \
  -c copy -map 0:v:0 -map 1:a:0 -f hls -hls_time 4 -hls_list_size 0 \
  -hls_playlist_type event -hls_segment_filename 'seg%04d.ts' \
  -hls_flags independent_segments+temp_file stream.m3u8
```

Use `-hls_playlist_type event` (not `vod`) so m3u8 grows as segments are generated. hls.js reloads the playlist to discover new segments.

### Choosing a mode

| Mode | Startup | Seeking | Best for |
|------|---------|---------|----------|
| **Virtual MP4 Proxy** (v3) | **~8s** (14min video) | ✅ Full seek, ~5s accuracy | **Long videos — no full download!** ✅ WORKING |
| Faststart MP4 (bili_cast.py) | ~8-10s short, ~40s+ long | ✅ Full, frame-accurate | Short videos, or fallback |
| HLS via browser (hls.js) | ~3-5s | ✅ Within generated segments | Choppy on C8 (Chrome 53 too slow) |
| Fragmented MP4 (photovideo) | ~3s | ❌ None | When seeking not needed |
| Raw M4S proxy | ~1s | ❌ "function not available" | Does NOT work on C8 |

### DLNA TimeSeekRange — DOES NOT WORK on LG C8
Tested 2026-03. C8 (webOS 4.5) only sends `Range: bytes=X-` (byte-range seek), never `TimeSeekRange.dlna.org`. Setting `DLNA.ORG_OP=10` causes broken seek behavior. Confirmed by Gerbera#839, UMS, JRiver forums. Plex works because it uses native webOS app + HLS/DASH, not DLNA.

### Proxy script (video core)
`scripts/mp4_proxy_v3.py` — builds a virtual progressive MP4 by parsing DASH moof/trun metadata and serving mdat bodies on demand from CDN. Called by `bili_cast.py` for video content.

- ✅ No full pre-download
- ✅ Seek/scrub works on LG C8 photovideo
- ✅ Audio/video stable (fixed default_sample_duration from tfhd)
- ✅ Long video latency optimized with parallel moof scan

Measured initialization latency (2026-03):
- ~14min video (170 seg): **~8s**
- ~58min video (701 seg): **~13s** (20 workers)

Usage:
```bash
python3 ~/.hermes/skills/media/bilibili-cast-to-tv/scripts/mp4_proxy_v3.py "https://b23.tv/xxxxx" [tv_ip] [format_selector] [workers]
```

Examples:
```bash
# H.264 1080p + best AAC (default)
python3 ~/.hermes/skills/media/bilibili-cast-to-tv/scripts/mp4_proxy_v3.py "https://b23.tv/xxxxx"

# explicit TV IP
python3 ~/.hermes/skills/media/bilibili-cast-to-tv/scripts/mp4_proxy_v3.py "https://b23.tv/xxxxx" <TV_IP>

# HEVC if desired
python3 ~/.hermes/skills/media/bilibili-cast-to-tv/scripts/mp4_proxy_v3.py "https://b23.tv/xxxxx" <TV_IP> "30077+30280"

# tune worker count (default 20)
python3 ~/.hermes/skills/media/bilibili-cast-to-tv/scripts/mp4_proxy_v3.py "https://b23.tv/xxxxx" <TV_IP> "30080+30280" 12
```

## Virtual MP4 Proxy (PROVEN — for long videos without full download)

Goal: Present a seekable progressive MP4 to the TV while fetching data on-demand from Bilibili CDN. No full download needed.

### How it works

B站 uses **single-file DASH** (SegmentBase). Each M4S is a complete fragmented MP4:
`[ftyp][moov][sidx][moof][mdat][moof][mdat]...`

The proxy:
1. **Fetches init segment** (ftyp+moov, ~1KB) + **SIDX** (~2KB) from video & audio M4S via Range requests
2. **Parses SIDX** to get all fragment boundaries (byte offset, size, duration per segment, typically 5s each)
3. **Extracts stsd** (codec config) from the fragmented moov
4. **Synthesizes a progressive moov** with sample tables (stts/stsz/stco/stss) at SIDX granularity — each segment = 1 "sample"
5. **Builds a virtual file layout**: `[ftyp][synth_moov][mdat_header][interleaved vid+aud segments]`
6. **Serves via HTTP with Range support** — header from memory, mdat ranges proxied to CDN on demand
7. **Caches fetched segments** in memory to avoid re-downloading

### Key technical details

- **stsd extraction**: Must extract from original fragmented moov (contains codec config like SPS/PPS for H.264, VPS for HEVC). Navigate: `moov > trak > mdia > minf > stbl > stsd`.
- **stco offsets**: Must be fixed AFTER moov is fully assembled, since moov size affects all offsets. Build moov first with placeholder zeros, then patch stco entries with correct virtual offsets from mdat_map.
- **Interleaving**: Virtual mdat alternates `[vid_seg0][aud_seg0][vid_seg1][aud_seg1]...` so both tracks have chunk offsets throughout the file.
- **SIDX-level granularity**: Seek accuracy ~5 seconds (segment boundary). Good enough for TV use.
- **CDN headers required**: `Referer: https://www.bilibili.com/` + User-Agent, else 403.

### Script: `/tmp/mp4_proxy.py`

```bash
# 1. Extract URLs and save to JSON
python3 -c "...yt-dlp -j -f 30080+30280..." > /tmp/proxy_urls.json
# 2. Run proxy
python3 /tmp/mp4_proxy.py
```

### Status (2026-03-28) — WORKING ✅

- ✅ Virtual MP4 validated by ffprobe AND ffmpeg full decode (1259+ frames, correct duration)
- ✅ **Plays on LG C8** with full seek support via photovideo
- ✅ Audio and video correct after tfhd default_sample_duration fix
- ✅ **Batched moof scanning: 8s init for 14-min video** (170 segments), down from 71s serial
- ✅ Parallel video+audio track init
- ✅ HTTP Range requests + segment caching (ThreadingHTTPServer)
- Script: `scripts/mp4_proxy_v3.py`

### Bug history and fixes

**v1 (SIDX-only, segments-as-samples):** Black screen. moof+mdat served as sample data — TV got moof box headers where it expected NAL units.

**v2 (strip moof, mdat body as one sample):** "file cannot be recognized". Whole mdat body (~1MB) treated as one stsz entry but contains many frames. ffprobe: "Invalid NAL unit size".

**v3 (full moof/trun parsing):** Correct approach. Script: `/tmp/mp4_proxy_v3.py`. Parses every trun for per-frame sizes/durations/sync-flags. Issues found and fixed:
- **CDN rate limiting**: 701 parallel Range requests → connection drops. Fix: serial fetch with `requests.Session` (connection reuse) + retry with session recreation on failure. ~140s for 701 moofs at ~0.2s each.
- **stsc bug**: Used `acc += sample_size` heuristic to count samples per chunk — wrong. Fix: store `samples_per_seg` from actual trun `sample_count` during moof parsing, use directly in stsc.
- **Audio corruption / non-monotonic DTS**: ffmpeg showed "non monotonically increasing dts" and audio had static/crackling. Likely caused by missing ctts box for B-frame reordering. trun has composition_time_offset per sample (flags & 0x800) — currently parsed but discarded. Need ctts box in progressive moov.

**Critical fix: tfhd default_sample_duration (THE fix that made it work)**
B站 trun flags = 0xa05 → `has_duration = False`. Samples use `default_sample_duration` from `tfhd` (typically 640 = 40ms = 25fps), NOT per-sample duration in trun. Without parsing tfhd defaults, all stts entries had duration=0 → ffprobe showed 19.96s instead of 850s → TV couldn't seek properly and audio was corrupted. Fix: parse tfhd box in each traf to get default_duration/size/flags, pass as defaults to parse_trun.

**Critical fix: batched moof scanning**
Serial 701 Range requests took 140s and B站 CDN rate-limited (dropped connections). Fix: batch 50 segments per Range request. Each batch fetches from seg[i] start to seg[i+49] start + 4KB — downloads mdat data in between but only parses the moof headers at known SIDX offsets. Reduces 701 requests to ~15, total time ~8s for 170 segments. Parallel init of video+audio tracks halves wall time further.

**Remaining issues:**
- **stco 32-bit overflow**: Files >4GB need co64. Current code uses stco (max ~4GB).
- **CDN URL expiry**: 2-hour deadline. Long playback needs URL refresh.
- **Long video init time**: 58-min video (701 segments) estimated ~30s with batching (untested after fix). Video batches are larger (segments ~1MB each, so 50-segment batch = ~50MB download per request) which may be slow.

### Raw M4S CDN Proxy — DOES NOT WORK on C8

Tested 2026-03-28. Proxied B站's native M4S file (which has SIDX) directly to photovideo. Result: **"this function is not available right now"** when trying to seek. C8's photovideo app **cannot use SIDX for seeking** in fragmented MP4 — it only supports byte-range seeking in progressive MP4 with moov+stbl sample tables. This confirms the progressive MP4 proxy (v3) is the only viable approach for seekable on-demand streaming.

### Key lesson: fMP4 → progressive MP4 conversion is hard

The core difficulty: progressive MP4's mdat must contain **only raw sample data** (H.264 NAL units). The TV uses stsz to know each frame's byte size and slices mdat accordingly. But fMP4 segments contain `[moof][mdat]` pairs — the moof box headers ARE NOT sample data. You cannot just concatenate segment data into a progressive mdat; the moof headers corrupt the stream. Must either:
1. Strip moof headers and serve only mdat body content (requires knowing each moof's exact size → scanning)
2. Use fMP4 natively with SIDX (the raw proxy approach)

### B站 M4S file structure reference
```
[ftyp]     32 bytes        File type
[moov]     ~1KB            Fragmented moov (mvhd + trak with mvex, NO sample tables)
[sidx]     ~2KB            Segment index (N refs × 12 bytes, N = duration/5s)
[moof]     ~1KB            Movie fragment header (contains trun with per-sample sizes)
[mdat]     varies          Media data for this fragment
[moof]     ...             Next fragment
[mdat]     ...
...
```

SIDX ref entry (12 bytes): `[ref_type(1b)+ref_size(31b)][subsegment_duration(32b)][SAP flags(32b)]`

## Future Improvements

- [x] ~~Raw M4S proxy~~: Tested — C8 photovideo CANNOT seek in fMP4 with SIDX. "this function is not available right now"
- [x] ~~Progressive proxy v3~~: Working! tfhd default_sample_duration fix resolved audio/video issues.
- [x] ~~Progressive proxy speed~~: Parallel moof scan with 20 workers: 13s for 58-min video (701 segments). Down from 5 min serial.
- [ ] **HEVC in proxy**: C8 supports HEVC Main/Main10. Research confirmed. Would reduce CDN transfer ~60%. Not yet tested with proxy.
- [ ] Virtual MP4 Proxy: add co64 support for files >4GB
- [ ] Virtual MP4 Proxy: CDN URL refresh for videos >2 hours
- [ ] Register as Hermes tool for direct invocation
- [ ] Subtitle support (yt-dlp `--write-subs` + burn-in or separate serve)

## Bilibili Live Stream Casting

Handled by `scripts/bili_cast.py` (unified script — auto-detects live).

### How it works
- yt-dlp has a `BiliLive` extractor that returns m3u8 + FLV stream URLs
- B站直播的 m3u8 (fMP4/HLS) 可以**直接推给 C8 photovideo**，MIME 用 `application/vnd.apple.mpegurl`
- 不需要代理、ffmpeg、本地 HTTP 服务器
- 启动时间 ~2-3 秒

### Stream selection priority
1. AVC m3u8 (safest for C8)
2. HEVC m3u8 (C8 supports HEVC Main)
3. AVC FLV (needs remux — not yet implemented)

### Known limitations
- CDN URL 含 `expires=` 参数，可能 1-2 小时过期（直播一般不会太长）
- 不带 cookie 通常只拿到"超清"；带大会员 cookie 可能拿到"原画/蓝光/4K"
- HLS 天然 5-15 秒延迟（分片机制），弹幕同步不适合
- FLV-only 直播间暂不支持（C8 photovideo 不能播 FLV）
- [ ] YouTube casting via same pipeline
- [ ] Playback control (pause/play/stop) via SSAP after cast starts
