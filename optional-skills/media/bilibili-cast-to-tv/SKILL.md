---
name: bilibili-cast-to-tv
description: "Cast Bilibili videos + live streams to LG webOS TV. Unified script: auto-detects live (m3u8 direct push ~2s) vs video (virtual MP4 proxy ~8-15s). Authenticated via bili-cli for 4K/1080高码率."
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [bilibili, casting, lgtv, ffmpeg, yt-dlp, streaming, media]
---

# Bilibili Video Casting to LG TV

Cast Bilibili (B站) videos to an LG webOS TV on the local network. Two modes:
1. **Seekable mode** (full download): yt-dlp full file → faststart remux → serve. Supports scrubbing/seeking, but startup grows with video length.
2. **Proxy mode (preferred for daily use)**: virtual MP4 proxy (`mp4_proxy_v3.py`) with on-demand CDN fetch. No full pre-download, full seek support, ~8-15s init on tested videos.

## Default policy (for this setup)

- User preference: **do not wait for full download before casting**. Prefer proxy mode first.
- **CDN mirror speed varies wildly between requests** — `mp4_proxy_v3.py` calls B站 playurl API directly (not yt-dlp) and speed-tests all mirrors before picking. Never assume one mirror is "always fast"; same video can flip 5-20x between requests (e.g. cosov=122Mbps/akam=11Mbps one minute, akam=45Mbps/cosov=6Mbps the next).
- Quality priority for C8 — **depends on the source video's frame rate AND codec**:
  - **4K @ ≤30fps** → `30120` (AVC High@L5.1) ✅ plays fine
  - **4K @ 60fps** → `30121` (HEVC 4K60) ✅ — C8's HEVC decoder handles Main/Main10 4K60 in hardware. Requires `hev1`→`hvc1` tag rewrite (done by `_normalize_hevc_stsd()` in mp4_proxy_v3). **Do NOT use `30120` for 4K60** — that's AVC High@L5.2 which C8 cannot decode → "this file cannot be recognized".
  - **No 4K available** → `30116` (1080P60) → `30112` (1080高码率) → `30080` (1080) → `30064` (720)
  - C8's AVC hardware decoder caps at **High@L5.1**. ffprobe the first 2MB to read the actual `level` field (51 = L5.1, 52 = L5.2). Frame rate is the practical proxy: `frameRate` field in playurl response > 30 ⇒ AVC L5.2 ⇒ pick HEVC instead.
  - **HEVC bitrate bonus**: HEVC streams are typically ~50% smaller than AVC at the same visual quality (B站 example: 4K60 AVC 23.7 Mbps vs HEVC 10.7 Mbps), so HEVC also doubles your bandwidth headroom from US to B站 CDN.
- Always authenticate via `bili login` credential → generate Netscape cookie file → pass to yt-dlp with `--cookies`.
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
python3 bili_cast.py "https://b23.tv/xxxxx" 192.168.1.102 30120+30280 20

# Live stream (auto-detected, m3u8 direct push):
python3 bili_cast.py "https://live.bilibili.com/103"
python3 bili_cast.py "https://b23.tv/V0VnBNa"
```

- **Video** → auto-selects best AVC format, delegates to `mp4_proxy_v3.py` (virtual proxy, ~8-15s init)
- **Live** → extracts m3u8 URL via yt-dlp `BiliLive` extractor, pushes directly to TV photovideo (~2s)
- Auto-reads `~/.bilibili-cli/credential.json` for authenticated cookies (4K/1080高码率)
- Default TV: 192.168.1.102 (Living Room C8)

### `scripts/mp4_proxy_v3.py` — Video proxy core

Virtual progressive MP4 proxy. Called by `bili_cast.py` for video content. Can also be used standalone.

### Cookie Management (critical for 4K / 1080P高码率)
- Preferred auth source: `bili login` → `~/.bilibili-cli/credential.json`
- ⚠️ **CRITICAL: cookie key case mismatch.** `credential.json` stores lowercase keys (`sessdata`, `bili_jct`, `dedeuserid`, `ac_time_value`, `buvid3`), but B站 API requires **uppercase** cookie names (`SESSDATA`, `DedeUserID`). Lowercase cookies are silently ignored → API returns `isLogin: False` → no VIP qualities.
  - Verify with `https://api.bilibili.com/x/web-interface/nav` — should return `isLogin: True`, `vipStatus: 1`, `vip.label.text: '年度大会员'`. If `isLogin: False`, your cookie names are wrong.
  - Correct mapping:
    ```python
    cred = json.load(open('~/.bilibili-cli/credential.json'))
    cookies = {
        'SESSDATA': cred['sessdata'],
        'bili_jct': cred['bili_jct'],
        'DedeUserID': str(cred['dedeuserid']),
        'buvid3': cred.get('buvid3','') or 'AAAAA',
    }
    ```
- For `yt-dlp`, convert credential into a **Netscape cookie file** with **uppercase** cookie names and pass with `--cookies <file>`:
  ```
  .bilibili.com	TRUE	/	FALSE	0	SESSDATA	<sessdata>
  .bilibili.com	TRUE	/	FALSE	0	bili_jct	<bili_jct>
  .bilibili.com	TRUE	/	FALSE	0	DedeUserID	<dedeuserid>
  ```
- ⚠️ Do **not** rely on a manually concatenated `Cookie:` header only; this can falsely miss `4K(120)` / `1080P高码率(112)` even when account is annual VIP
- Cookie file: prefer temp path (`/tmp/...`) and avoid committing into repositories

### Verifying real 4K vs AI-upscaled (and C8 4K limitation)

When yt-dlp reports "4K is missing" but the official B站 app shows 4K available, query playurl directly:

```python
url = f'https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={cid}&qn=120&fnval=4048&fnver=0&fourk=1'
r = requests.get(url, cookies=cookies, headers={
    'User-Agent':'Mozilla/5.0 ... Chrome/120.0 ...',
    'Referer':f'https://www.bilibili.com/video/{bvid}/',
})
# Inspect data.dash.video[] for id=120
```

**Interpretation:**
- `id=120` present with codec `avc1.640034` (H.264 High@L5.2) → **real native 4K** uploaded by author
- `id=120` only via `100029` (av01.*) or only at lower frame rate than the 1080P → likely AI super-resolution
- `id=120` absent entirely → no 4K source

**LG C8 4K AVC support is level-dependent — verified via ffprobe (2026-04).** C8's hardware AVC decoder works at **High@L5.1** (4K30) but fails at **High@L5.2** (4K60). The codec string from playurl tells you which:

| Codec string | AVC level | Typical use | C8 plays? |
|---|---|---|---|
| `avc1.640033` | High@L5.1 | 4K @ 24/25/30fps | ✅ YES |
| `avc1.640034` | High@L5.2 | 4K @ 50/60fps | ❌ NO — "cannot be recognized" |
| `avc1.640032` | High@L5.0 | 1080P60, 1080P high-bitrate | ✅ YES |

The hex digit after `64003` = level. Read from `playurl.dash.video[].codecs`. Or ffprobe the first 2 MB and check `streams[0].level` (`51` vs `52`).

**Confirmed examples (2026-04):**
- BV1emFUzyEtS (Ho Chi Minh, 4K23.976, L5.1, 6 Mbps) → ✅ plays smooth
- BV18aFszBEo4 (Yubeng, 4K59.94, L5.2, 23.7 Mbps) → ❌ "cannot be recognized"

**The frame rate alone is a reliable proxy** without needing to ffprobe: B站's 4K AVC encoder uses L5.1 for ≤30fps and L5.2 for >30fps.

Workarounds for C8 when 4K is L5.2:
- Fall back to 1080P60 (id=116, AVC L4.2) — visually nearly identical for non-cinema content on a 48–55" panel
- **Preferred: HEVC 4K60** (id=120 with `codecs=hev1.*`, format `30121`) — confirmed working on C8 2026-04 with BV18aFszBEo4 (雨崩, 4K59.94 HEVC Main L5.1 10.7 Mbps). The proxy's `_normalize_hevc_stsd()` must rewrite the `hev1` sample entry tag to `hvc1` or webOS rejects it with "cannot be recognized". HEVC is also ~50% smaller than equivalent AVC, which halves bandwidth pressure on the US→B站 CDN path.
- HDMI direct from PC for true 4K60 AVC

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

### Why call playurl API directly instead of using yt-dlp's URL?
- yt-dlp's `requested_formats[].url` only returns ONE mirror URL (the first one in B站's API response)
- B站 returns 2-4 mirrors and **rotates the order between requests** — yt-dlp can't see the alternatives
- For US users, hitting the wrong mirror = 5-20x slower → 4K stutters. Need to speed-test every time.
- Solution: bypass yt-dlp's URL extraction, call `https://api.bilibili.com/x/player/playurl?...&fnval=4048&fourk=1` ourselves, get `dash.video[].baseUrl + dash.video[].backupUrl[]`, probe each with a small Range request, pick fastest.
- We still use yt-dlp via `bili_cast.py` for the auto-format detection step (it parses video metadata to find best AVC format ID), but `mp4_proxy_v3.py` does its own URL resolution.

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

## CDN Mirror Selection (critical for 4K playback)

B站 `playurl` API returns **multiple CDN mirrors** per quality — usually 2-3
of: Tencent Cloud (`upos-sz-mirrorcosov.bilivideo.com`), Akamai
(`upos-hz-mirrorakam.akamaized.net`), Alibaba Cloud, Huawei Cloud, etc.
The **order is randomized per call** and individual mirror speeds from
US (Irvine) fluctuate **wildly** between calls:

- Call 1: cosov 122 Mbps, akam 11 Mbps
- Call 2 (minutes later): akam 45 Mbps, cosov 6 Mbps

yt-dlp always picks the first URL in the `requested_formats[].url` field,
so you get a ~50/50 roll of the dice. For 1080P (≤4 Mbps) this is invisible;
for **4K AVC (~12 Mbps)** it's the difference between smooth playback and
constant rebuffering. The B站 mobile app doesn't stutter because it probes
and switches mirrors on the fly.

**Fix implemented in `mp4_proxy_v3.py resolve_urls_from_bilibili()` (2026-04):**
bypass yt-dlp's URL selection entirely. Call `api.bilibili.com/x/player/playurl`
directly (with cookies for VIP/4K access), collect `baseUrl` + `backupUrl` for
video and audio streams, race a 4 MB Range probe across all mirrors with a 6s
timeout, and feed the fastest URL to the proxy. yt-dlp is still used by
`bili_cast.py` for auto-format discovery, just not for the playback URL.

**Do not cache mirror preferences** — the winner changes every few minutes.
Always probe fresh on each cast.

### MirrorPool: hidden mirrors + per-connection throttling (2026-04)

Two more findings turned a working-but-stuttering 4K30 AVC pipeline into
a smooth one:

**1. The playurl API hides faster mirrors from overseas clients.**
For US callers it typically only returns 2 mirrors (Akamai + Tencent
海外), but the *same URL path* works on many other Bilibili CDN hosts
that the API never advertises. Discovered by manually substituting host
names in the baseUrl. Speed-tested working extras (2026-04):

| Host | Notes |
|---|---|
| `cn-hk-eq-01-02.bilivideo.com` | HK edge, often fastest from US west |
| `upos-sz-mirrorali.bilivideo.com` | Aliyun 国内 |
| `upos-sz-mirrorhw.bilivideo.com` | Huawei Cloud |
| `upos-sz-mirror08c.bilivideo.com` | unknown provider, often fast |
| `upos-sz-mirrorbd.bilivideo.com` | Baidu Cloud |
| `upos-sz-mirrorcos.bilivideo.com` | Tencent Cloud 国内 |
| `upos-sz-mirroraliov.bilivideo.com` | Aliyun 海外 |

These are hardcoded as `_EXTRA_MIRROR_HOSTS` in `mp4_proxy_v3.py`. The
`MirrorPool.probe()` method tests every API-returned mirror PLUS every
extra host (parallel 2 MB Range probes, 5 s timeout) and keeps the alive
ones sorted by measured Mbps. Typically 6–9 of 9 are alive per probe.

**2. B站 CDN throttles each TCP connection after ~3–5 MB sustained transfer.**
A single connection to even the fastest mirror caps around 25 Mbps from
US. 4K AVC at 15 Mbps ostensibly fits, but the throttle ramp causes
stutters. Fix: split large fetches into many small chunks (~1.5 MB
each, well under the throttle ceiling) and serve them from a shared
work-stealing queue across the top N mirrors with `Connection: close`
on every request (forces fresh TCP, no throttle state carried over).

`MirrorPool.fetch(start, end)` automatically:
- Fetches small reads (< 800 KB) from the single fastest mirror, serial.
- Splits large reads into ⌈length / 1.5 MB⌉ chunks (clamped to 8 max
  workers), dumps them in a `queue.Queue`, and spawns one worker thread
  per top-N mirror. Workers pull chunks until the queue empties — fast
  mirrors naturally service more chunks; slow ones don't hold up the
  whole request because they only get one chunk.
- Each chunk request uses `Connection: close` to force a new TCP flow.
- Falls back to `_fetch_single()` (serial mirror rotation) for tiny
  reads or when the pool has only one mirror.

**Measured improvement on a 15 Mbps 4K30 AVC video from Irvine, US:**
- Old (single mirror, single connection): 11 Mbps (constant rebuffering)
- New (MirrorPool, work-stealing 8-way parallel): 60–85 Mbps sustained,
  individual 30 MB Range request → 85 Mbps peak

**Key tunables in `MirrorPool` class:**
```python
PROBE_BYTES = 2 * 1024 * 1024       # speed probe size
PROBE_TIMEOUT = 5.0                  # seconds
PARALLEL_THRESHOLD = 800_000         # don't bother parallelising below this
PARALLEL_MAX_CHUNKS = 8              # cap on concurrent TCP flows
PARALLEL_TARGET_CHUNK = 1_500_000    # ~1.5 MB — below B站 throttle ceiling
PARALLEL_MIN_CHUNK = 256 * 1024      # don't split below 256 KB
```

**Critical sizing rule:** if `chunk_size` after dividing length by
worker count drifts above `1.5 × PARALLEL_TARGET_CHUNK`, the loop adds
more chunks. Without this, a 50 MB request with 8 workers ends up with
6 MB chunks → throttle kicks in → throughput drops back to ~50 Mbps.

**Moof scanning (TrackInfo._scan_moofs) also benefits.** Each worker
thread now pins to one mirror (round-robin assignment) and reuses a
`requests.Session` for the many small (4 KB) moof header reads. Earlier
all workers hit the same mirror, saturating one host with 4 KB request
spam.

**MirrorPool replaces the URL string in TrackInfo.** `cdn_fetch()` is a
back-compat wrapper that accepts either a string (legacy) or a
MirrorPool instance and dispatches accordingly. `resolve_urls_from_bilibili()`
returns MirrorPool objects, not URL strings.

### Segment prefetch — mandatory for playback to use MirrorPool bandwidth (2026-04)

**MirrorPool alone does not fix stuttering.** Even with 9 mirrors probed at
100+ Mbps, a steady-state `read_range` loop can only sustain ~20 Mbps through
the proxy, because the read path is serial:

```
TV requests 64 KB → cache.get((tr, si)) → cache miss
  → cdn_fetch() blocks for the entire ~5-10 MB segment
  → returns 64 KB → next iteration repeats
```

While `cdn_fetch` is running, nothing else is downloading. The TV's HTTP
read thread is idle waiting, and no future segment fetches are in flight.
So the proxy output is throttled to (segment_size / segment_fetch_time),
which for a 9 MB segment fetched over a throttle-limited connection
lands around 20 Mbps — dangerously close to a 15-20 Mbps 4K bitrate.

**Symptom:** `eth2 RX` during playback ≈ video bitrate ± 1 Mbps, no
headroom. Any mirror jitter causes rebuffer. Measured empirically with
`RX1=$(grep eth2 /proc/net/dev | awk '{print $2}'); sleep 15; RX2=...`.

**Fix: background prefetch.** The `Cache` class now tracks in-flight
`Future` objects alongside completed entries, and exposes
`cache.prefetch(k, fn)` that submits a fire-and-forget fetch to a
dedicated 8-worker `ThreadPoolExecutor`. In `read_range()`, every
segment hit triggers `_trigger_prefetch(tr, si)` which fires off
`cache.prefetch((tr, si+1)), ..., (tr, si+4)` asynchronously. By the
time the TV actually reads segment `si+1`, it's already cached.

```python
class Cache:
    def __init__(self, max_mb=600, prefetch_workers=8):
        self.d = {}                    # completed: k -> bytes
        self.inflight = {}             # in-progress: k -> Future
        self._executor = ThreadPoolExecutor(max_workers=prefetch_workers)

    def get(self, k, fn):
        with self.lk:
            if k in self.d:
                # LRU refresh
                v = self.d.pop(k); self.d[k] = v; return v
            if k in self.inflight:
                fut = self.inflight[k]
            else:
                fut = self._executor.submit(self._wrap_fetch, k, fn)
                self.inflight[k] = fut
        return fut.result()            # block outside the lock

    def prefetch(self, k, fn):
        with self.lk:
            if k in self.d or k in self.inflight: return
            fut = self._executor.submit(self._wrap_fetch, k, fn)
            self.inflight[k] = fut

# In read_range() / serving path:
PREFETCH_AHEAD = 4
def _trigger_prefetch(tr, si):
    track = vmp4.vid if tr == 'v' else vmp4.aud
    for k in range(1, PREFETCH_AHEAD + 1):
        ni = si + k
        if ni < len(track.seg_mdat):
            cache.prefetch((tr, ni), _seg_fetcher(tr, ni))
```

**Key design points:**
- **In-flight dedup** via `self.inflight`: a concurrent `get()` and
  `prefetch()` for the same segment share the same Future — only one
  HTTP fetch happens. Without this, simultaneous prefetch + on-demand
  reads would double-download.
- **LRU position refresh** on hit: the previous FIFO eviction (drop
  `next(iter(self.d))`) would evict segments that are still hot under
  steady-state load. Pop-and-reinsert moves the key to the end of
  dict insertion order, which Python 3.7+ guarantees is preserved.
- **8 worker threads**: each prefetch occupies one worker thread
  *synchronously* (MirrorPool.fetch internally parallelises across up
  to 8 TCP flows, so each segment fetch is already fast). 8 workers
  means we can have ~8 segments warming concurrently without thread
  starvation — a buffer of ~60-80 MB across video + audio.
- **Cache size bumped to 600 MB** to hold the readahead buffer without
  prematurely evicting already-served segments the TV might rewind into.

**Measured (BV1q5XeBxEsC at 19.2 Mbps 4K30 over 9-mirror MirrorPool, Irvine CA):**
| Path                                  | sustained throughput | UX |
|---|---|---|
| Before prefetch (serial read_range)   | 20-22 Mbps            | stutter |
| After prefetch (PREFETCH_AHEAD=4)     | **57 Mbps**           | smooth  |

**Verification**: during playback, run
```bash
RX1=$(grep eth2 /proc/net/dev | awk '{print $2}'); sleep 15;
RX2=$(grep eth2 /proc/net/dev | awk '{print $2}');
echo $(( (RX2-RX1)*8/15/1000000 )) Mbps
```
Healthy readahead should show RX at 2-3× the video bitrate. If RX ≈
bitrate, prefetch is not firing — check that `read_range` is calling
`_trigger_prefetch` every segment.

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

Living Room TV (LG C8) = webOS 4.5 → use `com.webos.app.photovideo`.

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
- **pywebostv keys are stored per-IP AND must be saved after register().** The `get_tv_client()` helper in `tv_discovery.py` (fixed 2026-04):
  1. reads `lgtv_keys.json` and extracts the entry for the current IP
  2. if no entry exists, falls back to **any** other client_key in the file (webOS binds the key to the TV hardware, not to the IP — so a key from an old IP works fine after DHCP drift)
  3. calls `c.register(store)` — pywebostv mutates `store` in place if a new pairing happens
  4. **writes the (possibly new) key back to `lgtv_keys.json`** keyed by the current IP
  Previous versions skipped step 4, which meant every cast after DHCP drift triggered a "LG Remote App connection request" popup on the TV. Also normalizes legacy string entries (`"ip": "key"`) to dict form (`"ip": {"client_key": "key"}`).
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
- **C8 4K AVC support is level-dependent.** `avc1.640033` (High@L5.1, 4K@≤30fps) works fine. `avc1.640034` (High@L5.2, 4K@60fps) **always fails** with "this file cannot be recognized" — C8's hardware decoder caps at L5.1. Read the level from `playurl.dash.video[].codecs` (the hex digit after `64003`) or ffprobe the first 2 MB (`streams[0].level` = 51 vs 52). If a 4K video has `frameRate > 30`, fall back to `30116+30280` (1080P60) or try HEVC `30121+30280`. **Diagnosis history**: this took 3 misdiagnoses to nail down — first thought C8 couldn't decode 4K AVC at all, then thought it could, then found the L5.1 vs L5.2 split via ffprobe comparison of a working 4K24 video (BV1emFUzyEtS) vs a failing 4K60 video (BV18aFszBEo4). Always ffprobe before claiming "C8 can/can't decode X".
- **CDN mirror speed is non-deterministic — must speed-test every time.** B站 playurl API returns 2-4 mirrors (typically `upos-sz-mirrorcosov.bilivideo.com` Tencent + `upos-hz-mirrorakam.akamaized.net` Akamai). yt-dlp blindly picks the first one; the order is randomized server-side, so half the time it picks the slow one. Symptom: 4K plays smoothly on phone (B站 app does its own probing) but stutters via this proxy. **Fix: `resolve_urls_from_bilibili()` calls playurl API directly and probes all mirrors with a 4MB Range request before selecting.** Speed gap between mirrors is often 5-20x and flips between requests — never cache "fastest mirror". User connection from US to B站 CDN is the bottleneck, not the proxy code.
- **B站 CDN throttles each TCP connection after ~3–5 MB sustained transfer.** A single connection — even to the fastest mirror — caps around 25 Mbps from US. Symptom: probe shows 100 Mbps for 2 MB but a real 30 MB Range fetch only averages ~15 Mbps. Single-connection downloads of 4K AVC (~15 Mbps) appear "almost fast enough" but actually rebuffer constantly. **Fix in MirrorPool**: split fetches into ~1.5 MB chunks (under the throttle ceiling) and parallelise across multiple mirrors with `Connection: close` so each chunk gets a fresh TCP flow. Without `Connection: close`, requests reuse the same throttled socket via urllib3 pooling and the parallelism is wasted. See "MirrorPool" section.
- **The playurl API hides faster mirrors from overseas clients.** For US callers, the API typically only returns Akamai + Tencent 海外, but the same URL path works on many other Bilibili CDN hosts (cn-hk-eq-01-02, mirrorali, mirrorhw, mirror08c, mirrorbd, etc.) — these are bound to the path, not the host. `MirrorPool` includes 7 hardcoded extra hosts that get probed alongside the API ones. Adds 30%+ throughput on average. The CN domestic mirrors (mirrorcos, mirrorali) sometimes outperform Akamai from US west; HK edge (cn-hk-eq) is often the single fastest.
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

## Content sources that CANNOT be cast with this pipeline (DRM)

**Recognize these instantly and don't waste time sniffing network traffic.** The LG TV photovideo/mediadiscovery player has NO Widevine/PlayReady/FairPlay CDM, so any DRM-protected stream is unplayable even if you capture the m3u8/mpd — the segments themselves are encrypted.

| Source | DRM | Notes |
|--------|-----|-------|
| NCAA March Madness Live (ncaa.com/march-madness-live) | Widevine (Warner Media) | Even the "3-hour free preview" stream is DRM'd |
| TBS / TNT / truTV / Max / HBO | Widevine | All Warner Media properties |
| ESPN / ESPN+ / ABC sports | Widevine | Disney stack |
| Peacock (NBC/Olympics) | Widevine | |
| Paramount+ (CBS sports) | Widevine | |
| NBA League Pass / NFL+ / MLB.tv | Widevine + device allowlist | Some geo-locked too |
| YouTube TV / Hulu Live / Sling TV / Fubo | Widevine | |
| Netflix / Disney+ / Prime Video | Widevine L1 required | |

### Diagnostic signature (headless Chromium hitting a DRM'd player)

When I sniff the network during a play attempt and see these together, stop immediately:

- Analytics beacon with `currentTveState=notloggedin:remaining` (or similar TVE state)
- `eventType=video_connection` + `interaction=error_message_alert_shown`
- Console warning: `It is recommended that a robustness level be specified` (EME/Widevine probing)
- Player chrome renders (Mute/PIP/Minimize buttons appear) but **zero m3u8/mpd/ts requests** are made
- Player config served from `warnermediacdn.com`, `psm*`, `tvem.cdn.turner.com`, `bamgrid.com`, `playback.svcs.plus.espn.com`, etc.

This = DRM wall. Don't keep clicking AGREE/Play variants hoping for a free preview — the preview exists but still uses Widevine.

### What to tell the user instead

Offer these alternatives in order:

1. **HDMI from laptop** — open the site in normal Chrome on a logged-in laptop, full-screen, plug HDMI into the TV. Fastest, most reliable, works for any DRM source.
2. **Phone/tablet app + Chromecast/AirPlay** — official app's free-preview or authenticated stream will cast to a Chromecast/Apple TV. LG C8 has no native AirPlay; C1/C2+ (webOS 6+) have AirPlay 2.
3. **TV native app** — only if the TV's webOS version is new enough AND the app exists (e.g. C1+ has Max, ESPN, YouTube TV; C8/webOS 4.5 is too old for most).
4. **VPN + B站 live room** — for big US sports events sometimes a B站 user mirrors the stream in a 直播间; search for it and use `bili_cast.py` on the live URL. Hit-or-miss and usually lower quality.

Do NOT offer: "let me try harder to extract the m3u8". The m3u8 is extractable but the .ts segments are AES-128/SAMPLE-AES encrypted with a Widevine-wrapped key the TV can't unwrap.

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
python3 ~/.hermes/skills/media/bilibili-cast-to-tv/scripts/mp4_proxy_v3.py "https://b23.tv/xxxxx" 192.168.1.102

# HEVC if desired
python3 ~/.hermes/skills/media/bilibili-cast-to-tv/scripts/mp4_proxy_v3.py "https://b23.tv/xxxxx" 192.168.1.102 "30077+30280"

# tune worker count (default 20)
python3 ~/.hermes/skills/media/bilibili-cast-to-tv/scripts/mp4_proxy_v3.py "https://b23.tv/xxxxx" 192.168.1.102 "30080+30280" 12
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

**Critical fix: co64 for files >4 GB (2026-04)**
The original code emitted `stco` (32-bit chunk offsets), which crashed
with `struct.error: 'I' format requires 0 <= number <= 4294967295` on
any virtual MP4 larger than 4 GB. Hit on a 4.7 GB 4K30 AVC video
(BV1xxx 独自深入原始大山, 19 Mbps × 34 min). Fix: always emit `co64`
unconditionally — pack offsets as `>Q` (64-bit) instead of `>I` (32-bit),
and write 8 bytes per entry instead of 4. Cost: +4 bytes per chunk × 2
tracks ≈ 5 KB on a typical 700-segment video, negligible. The mdat
largesize header path was already implemented (`if total_mdat + 8 > 0xFFFFFFFF`
emits the 16-byte largesize variant), so co64 was the last missing piece
for >4 GB streaming. **`_fix_stco()` now scans for `b'co64'` boxes** and
patches with `struct.pack_into('>Q', ..., p+16+i*8, offs[i])`.

**Remaining issues:**
- **CDN URL expiry**: 2-hour deadline. Long playback needs URL refresh.
- **Long video init time**: 58-min video (701 segments) estimated ~30s with batching (untested after fix). Video batches are larger (segments ~1MB each, so 50-segment batch = ~50MB download per request) which may be slow.

### HEVC support — `hev1` vs `hvc1` codec tag (CRITICAL for LG TVs)

**Symptom:** HEVC video plays fine on PC ffplay/VLC, ffprobe says it's a clean HEVC stream, but LG webOS photovideo/mediadiscovery rejects it with "this file cannot be recognized." Tested 2026-04 on C8 with B站 4K60 HEVC content (`hev1.1.6.L153.90`).

**Root cause:** ISO/IEC 14496-15 defines two HEVC sample-entry codec tags that differ in *where parameter sets (VPS/SPS/PPS) live*:
- **`hvc1`** — VPS/SPS/PPS must be carried **out-of-band** in the `hvcC` config record inside the sample entry
- **`hev1`** — VPS/SPS/PPS may also be **inlined** in the mdat NAL stream

LG webOS players (and many other TV/CE devices) **only accept `hvc1`**. Even when the source `hev1` stream happens to put parameter sets in `hvcC` correctly (B站 always does), the four-byte tag itself causes rejection.

**Fix:** rewrite the codec tag bytes in the stsd sample entry. The fix is purely a **byte-level patch on the 4-byte tag** at the start of each visual sample entry — no other bytes change, no box sizes change, the hvcC stays intact. Implemented in `mp4_proxy_v3.py` `_normalize_hevc_stsd()`:

```python
def _normalize_hevc_stsd(stsd_bytes: bytes) -> bytes:
    buf = bytearray(stsd_bytes)
    if len(buf) < 16: return bytes(buf)
    entry_count = struct.unpack('>I', bytes(buf[12:16]))[0]
    p = 16
    for _ in range(entry_count):
        if p + 8 > len(buf): break
        esize = struct.unpack('>I', bytes(buf[p:p+4]))[0]
        if bytes(buf[p+4:p+8]) == b'hev1':
            buf[p+4:p+8] = b'hvc1'
        p += esize if esize > 0 else 8
    return bytes(buf)
```

Called from `extract_stsd()` after locating the original stsd box in the source moov.

**Verification command** (skip TV push, ffprobe the proxy locally):
```python
# Launch proxy with bogus TV IP so push fails fast but HTTP server stays up
subprocess.Popen(['python3', '.../mp4_proxy_v3.py', URL, '192.0.2.1', '30121+30280', '20'])
# Probe http://127.0.0.1:9234/video.mp4 with Range bytes=0-2097151
# ffprobe should report `codec_tag_string=hvc1` (not hev1) and codec_name=hevc
```

**Why selecting HEVC requires more than passing format=30121:** B站 playurl returns multiple `dash.video[]` entries with the **same `id`** (e.g. `id=120` for 4K) but different `codecs` strings (`avc1.640034`, `hev1.1.6.L153.90`, `av01.*`). The original `_select_video()` matched by id only and always returned the first entry (AVC). Fix: `_select_video(streams, target_id, want_codec_family)` matches both `id` and codec family (`avc` / `hev` / `av1`). The format-id → (qn, codec) mapping is:
```
30120: (120, 'avc')  30121: (120, 'hev')  30125/30126: (120, 'hev')  # HDR/DV variants
30116: (116, 'avc')  30117: (116, 'hev')
30112: (112, 'avc')  30114: (112, 'hev')
30080: (80,  'avc')  30077: (80,  'hev')
30064: (64,  'avc')  30066: (64,  'hev')
```

**Why prefer HEVC for 4K60 on C8:** C8's AVC hardware decoder caps at L5.1 → 4K60 AVC fails. C8's HEVC decoder supports Main/Main10 4K60 in hardware. HEVC also reduces bitrate ~50% vs AVC for the same quality (B站 4K60 example: `avc1.640034` 23.7 Mbps vs `hev1.1.6.L153.90` 10.7 Mbps), which doubles the bandwidth headroom.

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
- [x] ~~**HEVC in proxy**~~: Implemented 2026-04. mp4_proxy_v3 now handles HEVC by rewriting `hev1` sample-entry tags to `hvc1` in `_normalize_hevc_stsd()` (LG TVs reject `hev1`). Combined with playurl-API-direct format selection that matches qn AND codec family, format `30121+30280` now selects HEVC 4K60 on B站 videos. See HEVC section below.
- [x] ~~Cookie key case~~: verified — `build_yt_cookie_args()` correctly maps lowercase credential keys to uppercase B站 cookie names. Not the bug; the original "yt-dlp picks wrong mirror" issue was the actual cause and was fixed by bypassing yt-dlp URL resolution.
- [x] ~~**C8 auto-format: skip L5.2 4K and prefer HEVC 4K60**~~: `bili_cast.py cast_video()` auto-format now checks `frameRate > 30` at 4K and selects `30121+30280` (HEVC 4K60) instead of `30120` (AVC L5.2). Falls back to `30116` (AVC 1080P60) only if HEVC 4K is unavailable. Implemented 2026-04 in cast_video().
- [x] ~~**MirrorPool with hidden mirrors + parallel chunked fetch**~~: implemented 2026-04. `mp4_proxy_v3.py` ships `MirrorPool` class that probes API mirrors + 7 hardcoded extra hosts, picks alive ones by speed, and parallelises fetches > 800 KB across the top 8 mirrors with `Connection: close` and ~1.5 MB chunks (under B站's per-connection throttle). Replaces single-mirror string URLs throughout TrackInfo.
- [x] ~~**Segment prefetch** to break serial read bottleneck~~: implemented 2026-04. `Cache` now tracks in-flight Futures + LRU refresh, exposes `prefetch()`; `read_range()` fires `PREFETCH_AHEAD=4` background fetches on every segment hit. Lifts sustained playback throughput from ~20 Mbps to ~57 Mbps on a 9-mirror pool (the MirrorPool bandwidth was being wasted because the read path was synchronous).
- [ ] **C1 routing**: when casting to C1 (webOS 6+), allow 4K AVC L5.2 — C1's hardware supports it. Detect TV model via MAC lookup in `lgtv.yaml` or by querying webOS device info, then pick format accordingly.
- [ ] **Bandwidth-aware codec fallback**: when AVC codec is selected but `MirrorPool.probe()` reports the top mirror's speed is < 1.2× the AVC bitrate, automatically switch to the HEVC variant (which is typically half the bitrate). Currently only the C8 4K60 case triggers HEVC; `bili_cast.py` should also do it for any video where AVC fits the spec but the live CDN can't keep up. Implementation: in `cast_video()` after the auto-format pick, if the proxy returns a `bandwidth_estimate` field, compare and re-select.
- [ ] **Mirror probe caching across retries**: each cast probes 9 hosts (~5 s). For back-to-back casts of the same video, caching the alive list for 60 s would shave init time. Risk: stale data if a mirror dies. Probably not worth it.
- [x] ~~Virtual MP4 Proxy: add co64 support for files >4GB~~: implemented 2026-04. Always emit co64 instead of stco; `_fix_stco()` patches 64-bit offsets. Tested with 4.7 GB 4K30 AVC video.
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
