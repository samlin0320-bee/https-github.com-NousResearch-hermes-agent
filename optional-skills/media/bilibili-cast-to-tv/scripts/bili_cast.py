#!/usr/bin/env python3
"""Bilibili → LG TV caster. Auto-detects live streams vs videos.

Usage: python3 bili_cast.py <bilibili_url> [tv_ip] [format_selector] [workers]

  Live  → m3u8 direct push to TV photovideo (~2s, no proxy needed)
  Video → virtual MP4 proxy with on-demand CDN fetch (~8-15s)

Examples:
  python3 bili_cast.py https://live.bilibili.com/103
  python3 bili_cast.py https://b23.tv/tyRD3rx
  python3 bili_cast.py https://b23.tv/xxxxx 192.168.1.102 30120+30280 20
"""
import sys
import os
import json
import subprocess
import time
import re
import socket
from pathlib import Path

DEFAULT_TV_IP = '192.168.1.103'

# Import shared TV discovery
sys.path.insert(0, str(Path(__file__).parent))
from tv_discovery import resolve_tv_ip, get_tv_client, push_to_tv


# ── Cookie helper ────────────────────────────────────────────────────

def build_yt_cookie_args() -> list[str]:
    """Build yt-dlp --cookies args from bili-cli credential when available."""
    cred_path = Path.home() / '.bilibili-cli' / 'credential.json'
    if not cred_path.exists():
        return []
    try:
        cred = json.loads(cred_path.read_text())
    except Exception:
        return []

    cookie_map = {
        'SESSDATA': cred.get('sessdata') or cred.get('SESSDATA'),
        'bili_jct': cred.get('bili_jct') or cred.get('biliJct'),
        'DedeUserID': cred.get('dedeuserid') or cred.get('DedeUserID'),
        'buvid3': cred.get('buvid3'),
        'buvid4': cred.get('buvid4'),
        'ac_time_value': cred.get('ac_time_value'),
    }
    lines = ['# Netscape HTTP Cookie File', '']
    count = 0
    for k, v in cookie_map.items():
        if v:
            lines.append(f'.bilibili.com\tTRUE\t/\tTRUE\t2147483647\t{k}\t{v}')
            count += 1
    if count == 0:
        return []

    cookie_file = Path('/tmp/bili_cast.cookies.txt')
    cookie_file.write_text('\n'.join(lines) + '\n')
    return ['--cookies', str(cookie_file)]


# TV connection helpers imported from tv_discovery module above


# ── Content type detection ───────────────────────────────────────────

def detect_content_type(url: str) -> tuple[str, dict | None]:
    """Detect whether URL is a live stream or a video.

    Returns ('live', yt-dlp_json) or ('video', None).
    For short links, probes via yt-dlp and caches the result.
    """
    if re.search(r'live\.bilibili\.com/\d+', url):
        return 'live', None
    if re.search(r'bilibili\.com/video/', url) or re.search(r'bilibili\.com/bangumi/', url):
        return 'video', None

    # Short links (b23.tv) — probe with yt-dlp
    cookie_args = build_yt_cookie_args()
    r = subprocess.run(
        ['yt-dlp', '-j', '--no-warnings', *cookie_args, url],
        capture_output=True, text=True, timeout=30
    )
    if r.returncode != 0:
        return 'video', None

    data = json.loads(r.stdout)
    if data.get('is_live'):
        return 'live', data
    return 'video', None


# ── Live stream cast ─────────────────────────────────────────────────

def cast_live(url: str, tv_ip: str, cached_data: dict | None = None):
    """Cast a live stream via m3u8 direct push."""
    cookie_args = build_yt_cookie_args()
    has_cookies = bool(cookie_args)

    if cached_data:
        data = cached_data
    else:
        r = subprocess.run(
            ['yt-dlp', '-j', '--no-warnings', *cookie_args, url],
            capture_output=True, text=True, timeout=30
        )
        if r.returncode != 0:
            raise RuntimeError(f'yt-dlp failed: {r.stderr.strip()[:300]}')
        data = json.loads(r.stdout)

    if not data.get('is_live'):
        raise RuntimeError('Stream is not currently live')

    title = data.get('title', 'Live')
    formats = data.get('formats', [])

    # Prefer AVC m3u8 > HEVC m3u8
    m3u8_avc = [f for f in formats if f.get('protocol', '').startswith('m3u8') and f.get('vcodec') == 'avc']
    m3u8_hevc = [f for f in formats if f.get('protocol', '').startswith('m3u8') and f.get('vcodec') == 'hevc']
    chosen = (m3u8_avc or m3u8_hevc or [None])[0]

    if not chosen:
        raise RuntimeError(f'No m3u8 stream. Formats: {[f.get("format_id") for f in formats]}')

    print(f'   Title: {title[:60]}', flush=True)
    print(f'   Stream: {chosen.get("vcodec")} m3u8', flush=True)
    print(f'   Auth: {"yes (大会员)" if has_cookies else "guest"}', flush=True)
    print(f'   Method: m3u8 direct push (no proxy)', flush=True)

    client = get_tv_client(tv_ip)
    resp = push_to_tv(client, chosen['url'], title, mime='application/vnd.apple.mpegurl')
    success = resp.get('payload', {}).get('returnValue', False)
    return success, resp


# ── Video cast (delegates to mp4_proxy_v3) ───────────────────────────

def cast_video(url: str, tv_ip: str, format_selector: str, workers: int):
    """Cast a video via the virtual MP4 proxy."""
    if format_selector == 'auto':
        cookie_args = build_yt_cookie_args()
        r = subprocess.run(
            ['yt-dlp', '-J', '--no-warnings', *cookie_args, url],
            capture_output=True, text=True, timeout=45
        )
        if r.returncode != 0:
            raise RuntimeError(f'yt-dlp failed: {r.stderr.strip()[:300]}')

        data = json.loads(r.stdout)
        # C8 codec matrix (α9 Gen1 SoC, confirmed 2026-04):
        #   AVC/H.264:  4K ≤ 30fps ✅   4K @ 60fps ❌ (SoC limit, not profile/level/bitrate)
        #   HEVC/H.265: 4K ≤ 60fps ✅   (Main / Main10 both OK, up to L5.1)
        # Strategy: prefer HEVC at 4K60, otherwise prefer AVC (better compat + our
        # mp4_proxy is battle-tested on AVC).
        video_fmts = [f for f in data.get('formats', [])
                      if (f.get('height') or 0) > 0 and f.get('vcodec') and f.get('vcodec') != 'none']

        def _is_avc(f): return str(f.get('vcodec','')).startswith('avc1')
        def _is_hevc(f): return any(str(f.get('vcodec','')).startswith(p) for p in ('hev1','hvc1','hev','hvc'))
        def _fps(f): return f.get('fps') or 0
        def _h(f):   return f.get('height') or 0

        # Build candidate lists sorted by quality
        avc = sorted([f for f in video_fmts if _is_avc(f)],
                     key=lambda f: (_h(f), _fps(f), f.get('tbr', 0)), reverse=True)
        hevc = sorted([f for f in video_fmts if _is_hevc(f)],
                      key=lambda f: (_h(f), _fps(f), f.get('tbr', 0)), reverse=True)

        best_vid = None
        # Rule 1: if best AVC is 4K but > 30fps, try HEVC at same resolution/fps
        if avc and _h(avc[0]) >= 2160 and _fps(avc[0]) > 30:
            hevc_4k60 = next((f for f in hevc if _h(f) >= 2160 and _fps(f) >= 50), None)
            if hevc_4k60:
                best_vid = hevc_4k60['format_id']
                note = 'HEVC 4K60 (C8 AVC cannot do 4K60)'
            else:
                # HEVC 4K60 unavailable → fall back to AVC 1080P60
                avc_1080_60 = next((f for f in avc if _h(f) == 1080 and _fps(f) >= 50), None)
                if avc_1080_60:
                    best_vid = avc_1080_60['format_id']
                    note = 'AVC 1080P60 (no HEVC 4K60 available)'

        # Rule 2: default — best AVC
        if not best_vid and avc:
            best_vid = avc[0]['format_id']
            note = f'AVC {_h(avc[0])}p{int(_fps(avc[0]))}'
        if not best_vid:
            best_vid = '30080'
            note = 'AVC 1080P (fallback)'

        aud = sorted(
            [f for f in data.get('formats', [])
             if f.get('acodec', 'none') != 'none' and f.get('vcodec', 'none') == 'none'],
            key=lambda f: f.get('abr', 0), reverse=True
        )
        best_aud = aud[0]['format_id'] if aud else '30280'

        format_selector = f'{best_vid}+{best_aud}'
        print(f'   Auto format: {format_selector} ({note})', flush=True)

    # Delegate to mp4_proxy_v3.py (replaces this process)
    script = str(Path(__file__).parent / 'mp4_proxy_v3.py')
    cmd = [sys.executable, script, url, tv_ip, format_selector, str(workers)]
    print(f'   Delegating to proxy...', flush=True)
    os.execv(sys.executable, cmd)


# ── Main ─────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print('Usage: python3 bili_cast.py <url> [tv_ip] [format] [workers]', flush=True)
        print('', flush=True)
        print('Auto-detects live vs video:', flush=True)
        print('  Live  → m3u8 direct push (~2s)', flush=True)
        print('  Video → virtual MP4 proxy (~8-15s)', flush=True)
        print('', flush=True)
        print('Examples:', flush=True)
        print('  python3 bili_cast.py https://live.bilibili.com/103', flush=True)
        print('  python3 bili_cast.py https://b23.tv/tyRD3rx', flush=True)
        print('  python3 bili_cast.py https://b23.tv/xxx 192.168.1.102 30120+30280 20', flush=True)
        sys.exit(1)

    url = sys.argv[1]
    tv_ip_arg = sys.argv[2] if len(sys.argv) > 2 else None
    format_selector = sys.argv[3] if len(sys.argv) > 3 else 'auto'
    workers = int(sys.argv[4]) if len(sys.argv) > 4 else 20

    t0 = time.time()
    tv_ip = resolve_tv_ip(tv_ip_arg)
    print(f'🎬 Bilibili → TV ({tv_ip})', flush=True)
    print(f'   Detecting...', flush=True)

    content_type, cached_data = detect_content_type(url)
    print(f'   Type: {"🔴 LIVE" if content_type == "live" else "📹 Video"}', flush=True)

    if content_type == 'live':
        success, resp = cast_live(url, tv_ip, cached_data)
        dt = time.time() - t0
        print(f'📺 {"Playing!" if success else "Failed: " + str(resp)} ({dt:.1f}s)', flush=True)
        if success:
            print(f'🔴 Live stream active. Ctrl+C to stop.', flush=True)
            try:
                while True:
                    time.sleep(60)
            except KeyboardInterrupt:
                pass
    else:
        cast_video(url, tv_ip, format_selector, workers)


if __name__ == '__main__':
    main()
