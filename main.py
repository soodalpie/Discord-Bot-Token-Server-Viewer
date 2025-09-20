# relay_profiles_export.py
# pip install discord.py aiohttp

import os, re, json, base64, asyncio, typing, threading, sys, subprocess, math, time
import aiohttp
import discord
from pathlib import Path
from datetime import datetime, timezone
import html as _html
import re as _re

# ---------------- AppData & 저장소 ----------------
APPDIR_NAME = "채팅백업"
FILENAME = "token.json"
SALT = "t0k3n:"  # 단순 난독화(보안용 아님)
PAGE_SIZE = 5000

def get_appdata_dir(app_name: str = APPDIR_NAME) -> Path:
    if os.name == "nt":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
        p = Path(base) / app_name
    else:
        p = Path.home() / ".config" / app_name
    p.mkdir(parents=True, exist_ok=True)
    return p

STORE_DIR = get_appdata_dir()
STORE_PATH = STORE_DIR / FILENAME
EXPORT_DIR = STORE_DIR / "exports"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

# --- 언어 정규화 (hljs 별칭 매핑) ---
_LANG_ALIAS = {
    "py": "python", "gpy": "python",
    "js": "javascript", "node": "javascript", "nodejs": "javascript",
    "ts": "typescript",
    "sh": "bash", "shell": "bash", "zsh": "bash",
    "c#": "csharp", "cs": "csharp",
    "c++": "cpp", "hpp": "cpp", "h++": "cpp", "cc": "cpp", "hh": "cpp",
    "text": "plaintext", "txt": "plaintext",
}

def _msg_to_dict(m: discord.Message, av_class: str) -> dict:
    """가상 스크롤용 가벼운 행 데이터"""
    try:
        nick = getattr(m.author, "display_name", None) or getattr(m.author, "global_name", None) or getattr(m.author, "name", None) or str(m.author)
        uname = getattr(m.author, "name", None) or str(m.author)
        uid = getattr(m.author, "id", "")
        author_disp = f"{nick}({uname})[{uid}]"
    except Exception:
        author_disp = str(getattr(m, "author", ""))

    ts = getattr(m, "created_at", None) or datetime.now(timezone.utc)
    t = _to_local(ts)

    ref_txt = ""
    try:
        if m.reference and m.reference.resolved:
            rm = m.reference.resolved
            ref_txt = f"@{getattr(rm, 'author', '')}: {(rm.content or '')[:150]}"
    except Exception:
        pass

    atts = []
    try:
        for a in getattr(m, "attachments", []) or []:
            atts.append(getattr(a, "url", "") or "")
    except Exception:
        pass

    return {"u": author_disp, "t": t, "av": av_class, "txt": (m.content or ""), "ref": ref_txt, "att": atts}

def _norm_lang(lang: str) -> str:
    l = (lang or "").strip().lower()
    return _LANG_ALIAS.get(l, l)

def _render_text_html(text: str) -> str:
    """
    - ```lang\n ... ```  → <pre class="code-block"><code class="hljs language-xxx">...</code></pre>
    - `inline`          → <code>inline</code>
    - URL 자동 링크
    - 개행은 <br> (코드블럭 내부는 원본 유지)
    """
    text = text or ""

    # 1) 코드블럭을 먼저 빼서 플레이스홀더로 보관
    blocks = []
    def _take_block(m):
        lang = _norm_lang(m.group(1) or "")
        code = m.group(2) or ""
        idx = len(blocks)
        blocks.append((lang, code))
        return f"§§CODEBLOCK{idx}§§"

    text2 = _re.sub(r"```([^\n`]*)\n([\s\S]*?)```", _take_block, text)

    # 2) 일반 텍스트 이스케이프
    out = _html.escape(text2)

    # 3) 인라인 코드 `...`
    out = _re.sub(r"`([^`]+)`", lambda m: f"<code>{_html.escape(m.group(1))}</code>", out)

    # 4) URL 자동 링크
    out = _re.sub(
        r"(https?://[^\s<>()]+)",
        r"<a href='\1' target='_blank' rel='noopener'>\1</a>",
        out,
    )

    # 5) 개행
    out = out.replace("\n", "<br>")

    # 6) 코드블럭 복원 (언어 class와 hljs class 부여)
    for i, (lang, code) in enumerate(blocks):
        lang_cls = f" language-{_html.escape(lang)}" if lang else ""
        code_html = (
            "<pre class='code-block'>"
            f"<code class='hljs{lang_cls}'>"
            f"{_html.escape(code)}"
            "</code></pre>"
        )
        out = out.replace(f"§§CODEBLOCK{i}§§", code_html)

    return out

def _json_for_script(data: typing.Any) -> str:
    """JSON 문자열이 </script> 등으로 스크립트를 조기 종료하지 않도록 escape"""
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")

def _obf(s: str) -> str:
    return base64.b64encode((SALT + (s or "")).encode("utf-8")).decode("ascii")

def _deobf(s: str) -> str:
    try:
        d = base64.b64decode((s or "").encode("ascii")).decode("utf-8")
        return d[len(SALT):]
    except Exception:
        return ""

def load_profiles() -> list[dict]:
    if not STORE_PATH.exists():
        return []
    try:
        data = json.loads(STORE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("profiles"), list):
            return data["profiles"]
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []

def save_profiles(profiles: list[dict]) -> None:
    STORE_PATH.write_text(json.dumps({"profiles": profiles}, ensure_ascii=False, indent=2), encoding="utf-8")

def pick_profile_interactive(profiles: list[dict]) -> dict | None:
    if profiles:
        print("저장된 봇 목록:")
        for i, p in enumerate(profiles, 1):
            name = p.get("name") or "(이름 미지정)"
            bid = p.get("bot_id") or "-"
            print(f"  [{i}] {name}  (id={bid})")
        print("  [N] 새 프로필 추가")
        print("  [Q] 종료")
        sel = input("선택 (기본=1): ").strip() or "1"
        if sel.lower() == "q":
            return None
        if sel.lower() == "n":
            return {"__new__": True}
        try:
            idx = int(sel)
            if 1 <= idx <= len(profiles):
                return profiles[idx-1]
        except Exception:
            pass
        print("잘못된 선택입니다.")
        return None
    else:
        print("저장된 봇이 없습니다. 새 프로필을 만듭니다.")
        return {"__new__": True}

# ---------------- Discord 클라이언트 ----------------
intents = discord.Intents.default()
intents.guilds = True
intents.message_content = True
bot = discord.Client(intents=intents)

_queue: asyncio.Queue[discord.Message] = asyncio.Queue()
_session: typing.Optional[aiohttp.ClientSession] = None
_worker_task: typing.Optional[asyncio.Task] = None

TOKEN: str = ""
WEBHOOK_URL: str = ""
ACTIVE_PROFILE_KEY: str = ""  # 난독화 토큰 문자열

# 업로드 용량 한도(20MB)
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

# ---------------- 유틸 ----------------
def _open_folder_of(path: Path):
    """파일 저장된 폴더 열기"""
    try:
        folder = path if path.is_dir() else path.parent
        if os.name == "nt":
            os.startfile(str(folder))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])
    except Exception as e:
        print(f"[open folder warning] {e}")

def _truncate(s: str, limit: int) -> str:
    return s if len(s) <= limit else (s[: max(0, limit - 1)] + "…")

def _plainify(m: discord.Message) -> str:
    txt = (m.content or "")
    try:
        for u in getattr(m, "mentions", []) or []:
            txt = txt.replace(f"<@{u.id}>", f"@{u.display_name}")
            txt = txt.replace(f"<@!{u.id}>", f"@{u.display_name}")
        for r in getattr(m, "role_mentions", []) or []:
            txt = txt.replace(f"<@&{r.id}>", f"@{r.name}")
        for ch in getattr(m, "channel_mentions", []) or []:
            txt = txt.replace(f"<#{ch.id}>", f"#{ch.name}")
        txt = re.sub(r"<a?:([A-Za-z0-9_~]+):\d+>", r":\1:", txt)
    except Exception:
        pass
    return txt

def _jump_url(m: discord.Message) -> str:
    try:
        return f"https://discord.com/channels/{m.guild.id}/{m.channel.id}/{m.id}"
    except Exception:
        return getattr(m, "jump_url", "") or ""

def _avatar_url_from_user(user: discord.abc.User) -> str:
    try:
        av = user.display_avatar
        try:
            return str(av.with_format("png").with_size(128).url)
        except Exception:
            try:
                return str(av.replace(format="png", size=128).url)
            except Exception:
                return str(getattr(av, "url", "") or "")
    except Exception:
        return ""

def _format_webhook_username(m: discord.Message) -> str:
    # 닉네임(아이디)[숫자ID]
    try:
        nick = getattr(m.author, "display_name", None) or getattr(m.author, "global_name", None) or getattr(m.author, "name", None) or str(m.author)
        uname = getattr(m.author, "name", None) or str(m.author)
        uid = getattr(m.author, "id", "")
        u = f"{nick}({uname})[{uid}]"
        return _truncate(u, 80)
    except Exception:
        return _truncate(str(m.author), 80)

# ---------------- 실시간 복제(웹훅) ----------------
def _format_payload(m: discord.Message) -> dict:
    guild_name = getattr(m.guild, "name", "-")
    ch_name = getattr(m.channel, "name", str(m.channel) or "-")
    username = _format_webhook_username(m)
    avatar = _avatar_url_from_user(m.author)
    jump = _jump_url(m)

    ref_line = ""
    try:
        if m.reference and m.reference.resolved:
            rm = m.reference.resolved
            ref_line = f"↪️ @{getattr(rm,'author','')}: {(rm.content or '')[:120]}"
    except Exception:
        pass

    image_url = ""
    other_files = []
    for a in getattr(m, "attachments", []) or []:
        url = getattr(a, "url", "") or ""
        fn = getattr(a, "filename", "") or ""
        ct = (getattr(a, "content_type", "") or "").lower()
        is_img = (
            "image/" in ct
            or url.lower().split("?", 1)[0].endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))
            or fn.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))
        )
        if is_img and not image_url:
            image_url = url
        else:
            other_files.append(url or fn)

    lines = []
    if ref_line:
        lines.append(_truncate(ref_line, 256))
    body = _plainify(m)
    if body:
        lines.append(_truncate(body, 3500))
    if other_files:
        listed = "\n".join(f"• {u}" for u in other_files[:6])
        lines.append(_truncate(f"첨부:\n{listed}", 512))

    embed = {
        "title": _truncate(f"{guild_name} -->#{ch_name}", 256),
        "description": _truncate("\n".join(lines) or "(내용 없음)", 4096),
        "url": jump or None,
    }
    if image_url:
        embed["image"] = {"url": image_url}

    return {
        "username": username,
        "avatar_url": avatar,
        "embeds": [embed],
        "allowed_mentions": {"parse": []},
    }

async def _send_to_webhook(m: discord.Message):
    if not WEBHOOK_URL:
        return
    payload = _format_payload(m)
    assert _session is not None
    resp = await _session.post(WEBHOOK_URL, json=payload)
    if resp.status == 429:
        try:
            j = await resp.json()
            retry = float(j.get("retry_after", 1.0))
        except Exception:
            retry = 1.0
        await asyncio.sleep(max(0.5, retry))
    elif resp.status >= 400:
        txt = await resp.text()
        print(f"[webhook http {resp.status}] {txt[:200]}")

async def _webhook_worker():
    while True:
        msg = await _queue.get()
        try:
            await _send_to_webhook(msg)
        except Exception as e:
            print(f"[webhook error] {e}")
        finally:
            _queue.task_done()

# ---------------- HTML Export (단일 채널) ----------------
def _esc(s: str) -> str:
    return (
        str(s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )

def _to_local(ts: datetime) -> str:
    try:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)

def _is_image_attachment(a: discord.Attachment) -> bool:
    try:
        url = (a.url or "").lower()
        fn = (a.filename or "").lower()
        ct = (a.content_type or "").lower()
        if "image/" in ct:
            return True
        if url.split("?", 1)[0].endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
            return True
        if fn.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
            return True
    except Exception:
        pass
    return False

def _msg_to_html_block(m: discord.Message, av_class: str) -> str:
    # 닉네임(아이디)[숫자ID]
    try:
        nick = getattr(m.author, "display_name", None) or getattr(m.author, "global_name", None) or getattr(m.author, "name", None) or str(m.author)
        uname = getattr(m.author, "name", None) or str(m.author)
        uid = getattr(m.author, "id", "")
        author_disp = f"{nick}({uname})[{uid}]"
    except Exception:
        author_disp = str(getattr(m, "author", ""))

    ts = getattr(m, "created_at", None) or datetime.now(timezone.utc)
    t = _to_local(ts)
    body = _render_text_html(getattr(m, "content", "") or "")
    ref_html = ""
    try:
        if m.reference and m.reference.resolved:
            rm = m.reference.resolved
            ref_html = f"<div class='reply'>↪️ <b>{_esc(str(getattr(rm,'author','')))}</b>: {_esc((rm.content or '')[:150])}</div>"
    except Exception:
        pass
    att_htmls = []
    try:
        for a in getattr(m, "attachments", []) or []:
            url = _esc(getattr(a, "url", ""))
            fn = _esc(getattr(a, "filename", "") or url)
            if _is_image_attachment(a):
                att_htmls.append(
                    f"<div class='att'><a href='{url}' target='_blank' rel='noopener'>"
                    f"<img class='attimg' src='{url}' alt='{fn}'></a></div>"
                )
            else:
                att_htmls.append(f"<div class='att'><a href='{url}' target='_blank' rel='noopener'>{fn}</a></div>")
    except Exception:
        pass

    return f"""
    <div class='row'>
      <div class='avatar {av_class}'></div>
      <div class='msg'>
        <div class='hdr'><b>{_esc(author_disp)}</b> <span class='time'>{_esc(t)}</span></div>
        {ref_html}
        <div class='content'>{body}</div>
        {''.join(att_htmls)}
      </div>
    </div>
    """

_AVATAR_EMPTY_DATAURI = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII="

async def _fetch_avatar_data_uri(user: discord.abc.User) -> tuple[str, str]:
    """유저 아바타를 data URI로 반환. (mime, data_uri)"""
    url = _avatar_url_from_user(user)
    if not url:
        return ("image/png", _AVATAR_EMPTY_DATAURI)
    assert _session is not None
    try:
        async with _session.get(url) as resp:
            data = await resp.read()
            ct = resp.headers.get("Content-Type", "image/png").split(";")[0] or "image/png"
            b64 = base64.b64encode(data).decode("ascii")
            return (ct, f"data:{ct};base64,{b64}")
    except Exception:
        return ("image/png", _AVATAR_EMPTY_DATAURI)

async def export_channel_history_to_html(channel: discord.TextChannel) -> Path:
    print(f"[*] 내보내기 시작: {channel.guild.name}#{channel.name} ({channel.id})")
    rows: list[dict] = []
    count = 0
    start_ts = time.time()

    avatar_class_of: dict[int, str] = {}
    avatar_rule_of: dict[int, str] = {}

    async for m in channel.history(limit=None, oldest_first=True):
        uid = getattr(getattr(m, "author", None), "id", None)
        av_class = "av-0"
        if isinstance(uid, int):
            if uid not in avatar_class_of:
                _, data_uri = await _fetch_avatar_data_uri(m.author)
                cls = f"av-{uid}"
                rule = f".{cls}{{background-image:url({data_uri})}}"
                avatar_class_of[uid] = cls
                avatar_rule_of[uid] = rule
            av_class = avatar_class_of[uid]

        rows.append(_msg_to_dict(m, av_class))
        count += 1

        if count % 500 == 0:
            elapsed = time.time() - start_ts
            rate = count / elapsed if elapsed > 0 else 0
            print(f"  - 진행중... {count}개 처리, 경과 {elapsed:.1f}s, 속도 {rate:.1f} msg/s (유니크 아바타 {len(avatar_rule_of)})")

    elapsed_total = time.time() - start_ts
    print(f"[+] 수집 완료: {count}개, 경과 {elapsed_total:.1f}s (유니크 아바타 {len(avatar_rule_of)})")

    avatar_css = "\n".join(avatar_rule_of.values())
    title = f"{channel.guild.name}#{channel.name} 기록"

    hljs_css = "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark-dimmed.min.css"
    hljs_js  = "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"

    # 수집 완료 후, rows → 여러 개의 JSON 페이지로 쪼갭니다.
    pages = [rows[i:i+PAGE_SIZE] for i in range(0, len(rows), PAGE_SIZE)]
    data_scripts = "\n".join(
        f"<script type='application/json' id='page-{i}'>{_json_for_script(pg)}</script>"
        for i, pg in enumerate(pages)
    )
    TOTAL = len(rows)

    html = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)}</title>
<link rel="stylesheet" href="{hljs_css}">
<style>
:root {{
  --bg:#1f2125; --fg:#e6e6e6; --line:#2e3136; --link:#6cb8ff; --muted:#9aa1a8;
}}
* {{ box-sizing: border-box; }}
html, body {{ height: 100%; }}
body {{
  background: var(--bg); color: var(--fg);
  font-family: -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Apple SD Gothic Neo,Malgun Gothic,Noto Sans KR,Arial,sans-serif;
  margin: 0;
}}
/* 뷰포트 + 캔버스 */
#viewport {{ height: 100vh; overflow:auto; position:relative; }}
#canvas   {{ position:relative; width:100%; }}

/* 가상 스크롤 행: 고정 높이 */
.row {{
  position:absolute; left:0; right:0;
  height:28px; padding:4px 10px; border-bottom:1px solid var(--line);
  display:flex; align-items:center; gap:8px;
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
}}
.avatar {{ width:22px; height:22px; border-radius:50%; background:#2e3136; background-size:cover; background-position:center; flex:0 0 22px; }}
.user   {{ color:#93c5fd; }}
.time   {{ color:var(--muted); font-size:12px; margin-left:6px; }}
.ref    {{ color:#c9ced4; margin-left:8px; }}
.txt    {{ color:#e6e6e6; }}

/* 아바타 data URI 규칙 */
{avatar_css}
</style>
</head>
<body>
  <!-- 타이틀(고정) -->
  <div style="position:sticky;top:0;z-index:2;background:linear-gradient(#1f2125,#1f2125cc);border-bottom:1px solid var(--line);padding:10px 14px;">
    <b>{_esc(title)}</b>
    <span style="color:var(--muted);margin-left:8px;">총 {TOTAL:,}개</span>
  </div>

  <!-- 가상 스크롤 뷰 -->
  <div id="viewport"><div id="canvas"></div></div>

  <!-- 데이터 페이지: 스크롤 범위에 들어올 때만 파싱 -->
  {data_scripts}

  <script src="{hljs_js}"></script>
  <script>
  (function(){{
    const ROW_H = 28;                // 행 높이(고정)
    const PAGE_SIZE = {PAGE_SIZE};   // 한 페이지 당 아이템 수
    const TOTAL = {TOTAL};

    const viewport = document.getElementById('viewport');
    const canvas   = document.getElementById('canvas');
    canvas.style.height = (TOTAL * ROW_H) + 'px';

    // LRU 캐시
    const cache = new Map();   // pageNo -> array
    const order = [];
    const MAX_PAGES = 4;

    function getPage(pageNo){{
      if (cache.has(pageNo)){{
        const i = order.indexOf(pageNo); if (i>=0) order.splice(i,1);
        order.push(pageNo);
        return cache.get(pageNo);
      }}
      const el = document.getElementById('page-'+pageNo);
      if (!el) return [];
      const data = JSON.parse(el.textContent);
      cache.set(pageNo, data); order.push(pageNo);
      while (order.length > MAX_PAGES){{ const ev = order.shift(); cache.delete(ev); }}
      return data;
    }}

    // DOM 풀 & 온스크린 맵
    const pool = [];
    const onscreen = new Map(); // idx -> el
    function getRowEl(){{
      return pool.pop() || Object.assign(document.createElement('div'), {{className:'row'}});
    }}
    const ESC_RE = /[&<>]/g;
    const ESC_MAP = {{'&':'&amp;','<':'&lt;','>':'&gt;'}};
    function esc(s){{
      return String(s||'').replace(ESC_RE, function(ch){{ return ESC_MAP[ch]; }});
    }}

    function render(){{
      const top = viewport.scrollTop;
      const vh  = viewport.clientHeight;
      const first = Math.max(0, Math.floor(top / ROW_H) - 20);
      const last  = Math.min(TOTAL-1, Math.ceil((top + vh) / ROW_H) + 20);

      // 필요한 페이지 미리 파싱
      for (let i=first; i<=last; i+=PAGE_SIZE) getPage(Math.floor(i / PAGE_SIZE));

      // 제거
      for (const [idx, el] of Array.from(onscreen)) {{
        if (idx < first || idx > last) {{
          el.remove(); pool.push(el); onscreen.delete(idx);
        }}
      }}

      // 추가
      const frag = document.createDocumentFragment();
      for (let i=first; i<=last; i++) {{
        if (onscreen.has(i)) continue;
        const pageNo = Math.floor(i / PAGE_SIZE);
        const off = i % PAGE_SIZE;
        const pg = getPage(pageNo);
        const it = pg && pg[off]; if (!it) continue;

        const el = getRowEl();
        el.style.transform = 'translateY(' + (i * ROW_H) + 'px)';
        el.innerHTML =
          "<div class='avatar " + esc(it.av) + "'></div>" +
          "<span class='user'>" + esc(it.u) + "</span>" +
          "<span class='time'>" + esc(it.t) + "</span>" +
          (it.ref ? "<span class='ref'>↪ " + esc(it.ref) + "</span>" : "") +
          "<span class='txt'>" + esc(it.txt) + "</span>";
        onscreen.set(i, el); frag.appendChild(el);
      }}
      viewport.appendChild(frag);
    }}

    let raf = null;
    viewport.addEventListener('scroll', function(){{
      if (raf) cancelAnimationFrame(raf);
      raf = requestAnimationFrame(render);
    }});

    // 최초 진입 시 최신으로 이동(원치 않으면 주석)
    viewport.scrollTop = canvas.offsetHeight - viewport.clientHeight;
    render();
  }})();
  </script>
</body>
</html>"""

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    fname = f"log_{channel.guild.id}_{channel.id}_{ts}.html"
    path = EXPORT_DIR / fname
    path.write_text(html, encoding="utf-8")
    print(f"[+] HTML 저장: {path}  (size={path.stat().st_size/1024/1024:.2f} MB)")
    return path

# ---------------- HTML Export (서버 단위: export all) ----------------
async def export_guild_history_to_html(guild: discord.Guild) -> Path:
    """해당 길드의 모든 텍스트 채널을 하나의 HTML(사이드바+뷰)로 내보내기"""
    print(f"\n[*] 서버 내보내기 시작: {guild.name} (id={guild.id})")

    # 접근 가능한 채널만 대상
    channels: list[discord.TextChannel] = []
    for ch in getattr(guild, "text_channels", []):
        try:
            perms = ch.permissions_for(guild.me)
            if getattr(perms, "view_channel", False) and getattr(perms, "read_message_history", False):
                channels.append(ch)
        except Exception:
            continue

    # 정렬: 채널명
    channels.sort(key=lambda c: (c.name.casefold(), c.id))

    # 유저 아바타는 길드 전체에서 중복 제거
    avatar_class_of: dict[int, str] = {}
    avatar_rule_of: dict[int, str] = {}

    # 채널별 메시지 HTML
    ch_rows: dict[int, list[str]] = {}
    total_msgs = 0
    g_start = time.time()

    for ch in channels:
        print(f"  - 채널 수집 시작: #{ch.name} ({ch.id})")
        ch_rows[ch.id] = []
        start_ts = time.time()
        count = 0

        async for m in ch.history(limit=None, oldest_first=True):
            uid = getattr(getattr(m, "author", None), "id", None)
            av_class = "av-0"
            if isinstance(uid, int):
                if uid not in avatar_class_of:
                    _, data_uri = await _fetch_avatar_data_uri(m.author)
                    cls = f"av-{uid}"
                    rule = f".{cls}{{background-image:url({data_uri})}}"
                    avatar_class_of[uid] = cls
                    avatar_rule_of[uid] = rule
                av_class = avatar_class_of[uid]

            ch_rows[ch.id].append(_msg_to_html_block(m, av_class))
            count += 1
            total_msgs += 1

            if count and count % 500 == 0:
                elapsed = time.time() - start_ts
                rate = count / elapsed if elapsed > 0 else 0
                print(f"    · 진행중 #{ch.name}: {count}개, 경과 {elapsed:.1f}s, 속도 {rate:.1f} msg/s")

        print(f"  - 채널 완료: #{ch.name} / {count}개, 경과 {time.time() - start_ts:.1f}s")

    print(f"[+] 서버 수집 완료: 채널 {len(channels)}개, 메시지 {total_msgs}개, 총 경과 {time.time() - g_start:.1f}s (유니크 아바타 {len(avatar_rule_of)})")

    # HTML 조립
    avatar_css = "\n".join(avatar_rule_of.values())
    title = f"{guild.name} 서버 기록"

    # 네비게이션(사이드바)
    nav_items = []
    content_sections = []
    first_ch_id = channels[0].id if channels else None
    for ch in channels:
        count = len(ch_rows.get(ch.id, []))
        nav_items.append(f"<a class='ch-item' href='#ch-{ch.id}' data-ch='{ch.id}'>#{_esc(ch.name)} <span class='cnt'>{count}</span></a>")
        content_sections.append(f"<section id='ch-{ch.id}' class='chatview'>{''.join(ch_rows[ch.id])}</section>")

    hljs_css = "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark-dimmed.min.css"
    hljs_js  = "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"

    html = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)}</title>
<link rel="stylesheet" href="{hljs_css}">
<style>
:root {{
  --bg:#1f2125; --fg:#e6e6e6; --line:#2e3136; --link:#6cb8ff; --muted:#a4a9af;
  --sidebar:#17191d; --sidebar-line:#2a2d33; --accent:#3b82f6;
}}
* {{ box-sizing: border-box; }}
html, body {{ height: 100%; }}
body {{
  background: var(--bg); color: var(--fg);
  font-family: -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Apple SD Gothic Neo,Malgun Gothic,Noto Sans KR,Arial,sans-serif;
  margin: 0;
}}
.wrap {{ display:flex; min-height:100vh; }}
.sidebar {{
  width: 280px; flex: 0 0 280px;
  background: var(--sidebar);
  border-right: 1px solid var(--sidebar-line);
  position: sticky; top: 0; align-self: flex-start; height: 100vh; overflow:auto;
}}
.sidehdr {{ padding:14px 14px; border-bottom:1px solid var(--sidebar-line); font-weight:600; }}
.chlist {{ display:flex; flex-direction:column; padding:8px; gap:2px; }}
.ch-item {{
  display:block; padding:8px 10px; border-radius:6px; color:#d7dbe1; text-decoration:none;
}}
.ch-item:hover {{ background:#20232a; }}
.ch-item.active {{ background:#2a3140; color:#fff; }}
.ch-item .cnt {{ float:right; color:var(--muted); font-size:12px; }}
.main {{ flex: 1 1 auto; min-width:0; }}
.topbar {{ position: sticky; top:0; backdrop-filter: blur(6px); background: rgba(31,33,37,0.65); border-bottom:1px solid var(--line); padding:12px 16px; z-index:2; }}
.topbar h2 {{ margin:0; font-size:16px; }}
.contentarea {{ padding: 10px 14px; }}
.chatview {{ display:none; }}
.chatview.active {{ display:block; }}

.row {{
  display:flex; gap:12px; border-bottom:1px solid var(--line); padding:10px 0;
}}
.avatar {{
  width: 40px; height: 40px; border-radius: 50%;
  background: #2e3136; background-size: cover; background-position: center; flex: 0 0 40px;
}}
.msg {{ flex: 1 1 auto; min-width: 0; }}
.hdr {{ color: #9ba1a6; font-size: 12px; margin-bottom: 4px; }}
.hdr b {{ color: #fff; font-size: 14px; }}
.time {{ margin-left: 8px; }}
.reply {{ border-left: 3px solid #4e5058; color: #c9ced4; padding-left: 8px; margin: 4px 0; }}
.content {{ line-height: 1.5; word-break: break-word; overflow-wrap: anywhere; }}
pre.code-block {{
  background:#11151a; border:1px solid #2f343a; border-radius:8px; padding:10px 12px; overflow:auto;
  white-space:pre-wrap; word-break:break-word; margin:8px 0;
}}
.content code {{
  background:#1e1f22; border:1px solid #3f4147; border-radius:4px; padding:0 4px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace; font-size: 13px;
}}
.att a {{ color: var(--link); text-decoration: none; }}
.att a:hover {{ text-decoration: underline; }}
.attimg {{ max-width: 100%; height: auto; border-radius: 6px; display: block; margin-top: 6px; }}

@media (max-width: 900px) {{
  .sidebar {{ width: 220px; flex-basis: 220px; }}
}}
@media (max-width: 720px) {{
  .sidebar {{ position: fixed; z-index:3; left:0; top:0; height:100vh; transform: translateX(-100%); transition: transform .2s ease; }}
  .sidebar.open {{ transform: translateX(0); }}
  .hamb {{ display:inline-block; margin-right:8px; cursor:pointer; }}
}}
/* --- avatars (data URI) --- */
{avatar_css}
</style>
</head>
<body>
  <div class="wrap">
    <nav class="sidebar" id="sidebar">
      <div class="sidehdr">{_esc(guild.name)}</div>
      <div class="chlist">
        {''.join(nav_items) if nav_items else '<div style="padding:12px;color:#9aa1a8">표시할 채널이 없습니다.</div>'}
      </div>
    </nav>

    <main class="main">
      <div class="topbar">
        <span class="hamb" id="hamb" style="display:none">☰</span>
        <h2 id="curTitle">{_esc(guild.name)}</h2>
      </div>
      <div class="contentarea" id="contentArea">
        {''.join(content_sections)}
      </div>
    </main>
  </div>

  <script src="{hljs_js}"></script>
  <script>
  // 모바일에서 사이드바 토글
  (function(){{
    var hamb = document.getElementById('hamb');
    var sidebar = document.getElementById('sidebar');
    function onResize(){{
      if (window.innerWidth <= 720) {{
        hamb.style.display = 'inline-block';
      }} else {{
        hamb.style.display = 'none';
        sidebar.classList.remove('open');
      }}
    }}
    window.addEventListener('resize', onResize); onResize();
    hamb && hamb.addEventListener('click', function(){{ sidebar.classList.toggle('open'); }});
  }})();

  // 채널 활성화/타이틀 업데이트
  (function(){{
    var views = Array.from(document.querySelectorAll('.chatview'));
    var items = Array.from(document.querySelectorAll('.ch-item'));
    function activate(hash){{
      if (!hash) return;
      var id = hash.replace('#','');
      views.forEach(v => v.classList.toggle('active', v.id === id));
      items.forEach(i => i.classList.toggle('active', ('#ch-'+i.dataset.ch) === '#'+id));
      var activeItem = items.find(i => ('#ch-'+i.dataset.ch) === '#'+id);
      var title = activeItem ? activeItem.textContent.trim() : '{_esc(guild.name)}';
      document.getElementById('curTitle').textContent = title;
      // 모바일에서 클릭 시 사이드바 닫기
      var sb = document.getElementById('sidebar');
      if (window.innerWidth <= 720) {{ sb.classList.remove('open'); }}
    }}
    window.addEventListener('hashchange', function(){{ activate(location.hash || '#ch-{first_ch_id or ''}'); }});
    // 초기 활성화
    activate(location.hash || '#ch-{first_ch_id or ''}');
  }})();

  // highlight.js
  try {{
    document.querySelectorAll('pre.code-block code').forEach(function(codeEl) {{
      if (!codeEl.className.includes('hljs')) codeEl.classList.add('hljs');
    }});
    if (window.hljs && hljs.highlightAll) hljs.highlightAll();
  }} catch (e) {{}}
  </script>
</body>
</html>"""

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    fname = f"guild_log_{guild.id}_{ts}.html"
    path = EXPORT_DIR / fname
    path.write_text(html, encoding="utf-8")
    print(f"[+] HTML 저장(서버): {path}  (size={path.stat().st_size/1024/1024:.2f} MB)")
    return path

async def export_all_guilds_and_send():
    """봇이 들어간 모든 서버를 각각 하나의 HTML로 내보내고 웹훅 전송/안내"""
    if not bot.guilds:
        print("참여 중인 서버가 없습니다.")
        return
    for g in bot.guilds:
        try:
            path = await export_guild_history_to_html(g)
            header = f"[아카이브] {g.name} 서버 전체 기록 HTML"
            await send_file_via_webhook(path, header)
        except Exception as e:
            print(f"[export-all error] {g.name}: {e}")

# ---------------- 안내/전송 공통 ----------------
async def _send_text_notice_via_webhook(content: str):
    if not WEBHOOK_URL:
        print("⚠️ WEBHOOK_URL 비어 있음. 안내 전송 생략.")
        return
    assert _session is not None
    webhook = discord.Webhook.from_url(WEBHOOK_URL, session=_session)
    await webhook.send(content=content, wait=True)

async def send_file_via_webhook(file_path: Path, header_text: str):
    size = file_path.stat().st_size
    if size > MAX_UPLOAD_BYTES:
        msg = f"📦 파일이 너무 커서 로컬에 저장되었습니다.\n파일: **{file_path.name}**\n경로: `{str(file_path)}`"
        print(msg)
        _open_folder_of(file_path)
        await _send_text_notice_via_webhook(f"{header_text}\n{msg}")
        return

    if not WEBHOOK_URL:
        print("⚠️ WEBHOOK_URL 비어 있음. 전송 생략.")
        return

    assert _session is not None
    webhook = discord.Webhook.from_url(WEBHOOK_URL, session=_session)
    file = discord.File(str(file_path), filename=file_path.name)
    await webhook.send(content=header_text, file=file, wait=True)
    print(f"[+] 웹훅으로 파일 전송 완료: {file_path.name}")

# ---------------- 콘솔 명령 스레드 ----------------
def start_console_ui():
    threading.Thread(target=_console_loop, daemon=True).start()

def _console_loop():
    print("\n=== 콘솔 명령 ===")
    print("list               : 읽기 권한 있는 텍스트 채널 목록 표시")
    print("export <번호>      : 목록에서 번호로 선택해 HTML 내보내기 + 웹훅 전송")
    print("export id <채널ID> : 채널ID로 직접 내보내기")
    print("export all         : 봇이 들어간 모든 '서버'를 서버당 1개의 HTML로 내보내기")
    print("quit               : 종료(프로세스 강제 종료 권장 X)\n")

    indexed: dict[int, tuple[int,int,str,str]] = {}

    def refresh_list():
        nonlocal indexed
        fut = asyncio.run_coroutine_threadsafe(gather_channels(), bot.loop)
        chans = []
        try:
            chans = fut.result()
        except Exception as e:
            print(f"[list error] {e}")
            return
        indexed = {}
        print("\n--- 채널 목록 ---")
        for i, (g, ch) in enumerate(chans, 1):
            print(f"[{i}] {g.name}  #{ch.name}  ({ch.id})")
            indexed[i] = (g.id, ch.id, g.name, ch.name)
        if not chans:
            print("(표시할 채널 없음: 권한 또는 길드 없음)")
        print("-----------------\n")

    refresh_list()

    while True:
        try:
            cmd = input("> ").strip()
        except EOFError:
            return
        if not cmd:
            continue
        parts = cmd.split()
        if parts[0].lower() == "list":
            refresh_list()
        elif parts[0].lower() == "export":
            # export all
            if len(parts) == 2 and parts[1].lower() == "all":
                def _go():
                    fut = asyncio.run_coroutine_threadsafe(export_all_guilds_and_send(), bot.loop)
                    try:
                        fut.result()
                    except Exception as e:
                        print(f"[export-all error] {e}")
                threading.Thread(target=_go, daemon=True).start()
                print("[*] 서버 전체 아카이브를 백그라운드로 시작했습니다.")
            # export <번호>
            elif len(parts) == 2 and parts[1].isdigit():
                idx = int(parts[1])
                if idx not in indexed:
                    print("잘못된 번호입니다. 먼저 list를 실행해 번호를 확인하세요.")
                    continue
                _, cid, gname, cname = indexed[idx]
                _export_channel_id(cid, gname, cname)
            # export id <채널ID>
            elif len(parts) == 3 and parts[1].lower() == "id" and parts[2].isdigit():
                cid = int(parts[2])
                _export_channel_id(cid, None, None)
            else:
                print("사용법: export <번호>  |  export id <채널ID> | export all")
        elif parts[0].lower() == "quit":
            print("종료를 원하면 Ctrl+C 로 종료하세요.")
        else:
            print("알 수 없는 명령입니다. (list / export / export all / quit)")

def _export_channel_id(channel_id: int, gname: str | None, cname: str | None):
    async def _task():
        ch = bot.get_channel(channel_id)
        if ch is None:
            try:
                ch = await bot.fetch_channel(channel_id)
            except Exception as e:
                print(f"[fetch error] 채널 {channel_id} : {e}")
                return
        if not isinstance(ch, discord.TextChannel):
            print("텍스트 채널이 아닙니다.")
            return
        perms = ch.permissions_for(ch.guild.me)
        if not (getattr(perms, "view_channel", False) and getattr(perms, "read_message_history", False)):
            print("권한 부족: view_channel/read_message_history 필요")
            return
        path = await export_channel_history_to_html(ch)
        header = f"[아카이브] {ch.guild.name} → #{ch.name} 기록 HTML"
        await send_file_via_webhook(path, header)
    fut = asyncio.run_coroutine_threadsafe(_task(), bot.loop)
    try:
        fut.result()
    except Exception as e:
        print(f"[export error] {e}")

# ---------------- 이벤트 ----------------
@bot.event
async def on_ready():
    global _session, _worker_task
    if _session is None:
        _session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(_webhook_worker())

    me = bot.user
    print(f"✅ 로그인: {me} (id={getattr(me, 'id', '-')}) | Guilds={len(bot.guilds)}")

    if ACTIVE_PROFILE_KEY:
        profiles = load_profiles()
        updated = False
        for p in profiles:
            if p.get("token") == ACTIVE_PROFILE_KEY:
                p["name"] = str(me)
                p["bot_id"] = getattr(me, "id", None)
                updated = True
                break
        if updated:
            save_profiles(profiles)
            print(f"🔖 token.json 업데이트 완료: {STORE_PATH}")

    try:
        chans = await gather_channels()
        print("\n--- 채널 목록 ---")
        for i, (g, ch) in enumerate(chans, 1):
            print(f"[{i}] {g.name}  #{ch.name}  ({ch.id})")
        print("-----------------\n")
    except Exception as e:
        print(f"[list error] {e}")

    start_console_ui()

@bot.event
async def on_message(message: discord.Message):
    if (
        message.guild is None
        or message.webhook_id is not None
        or getattr(message.author, "bot", False)
        or not WEBHOOK_URL
    ):
        return
    await _queue.put(message)

async def gather_channels() -> list[tuple[discord.Guild, discord.TextChannel]]:
    out: list[tuple[discord.Guild, discord.TextChannel]] = []
    for g in bot.guilds:
        for ch in getattr(g, "text_channels", []):
            try:
                perms = ch.permissions_for(g.me)
                if getattr(perms, "view_channel", False) and getattr(perms, "read_message_history", False):
                    out.append((g, ch))
            except Exception:
                continue
    out.sort(key=lambda t: (t[0].name.casefold(), t[1].name.casefold(), t[1].id))
    return out

# ---------------- 메인 진입 ----------------
def _interactive_login() -> bool:
    global TOKEN, WEBHOOK_URL, ACTIVE_PROFILE_KEY
    profiles = load_profiles()
    choice = pick_profile_interactive(profiles)
    if choice is None:
        return False
    if choice.get("__new__"):
        tok = input("Bot Token: ").strip()
        wh = input("Webhook URL: ").strip()
        if not tok:
            print("토큰이 필요합니다.")
            return False
        TOKEN = tok
        WEBHOOK_URL = wh
        key = _obf(tok)
        ACTIVE_PROFILE_KEY = key
        profiles.append({"token": key, "webhook": wh, "name": None, "bot_id": None})
        save_profiles(profiles)
        print(f"💾 저장됨: {STORE_PATH}")
        return True
    else:
        tok = _deobf(choice.get("token", ""))
        if not tok:
            print("저장된 토큰을 해독할 수 없습니다.")
            return False
        TOKEN = tok
        WEBHOOK_URL = choice.get("webhook", "") or ""
        ACTIVE_PROFILE_KEY = choice.get("token", "")
        if not WEBHOOK_URL:
            print("⚠️ 이 프로필에는 Webhook URL이 비어 있습니다. 이후 전송이 동작하지 않을 수 있습니다.")
        return True

async def _amain():
    global _session
    ok = _interactive_login()
    if not ok:
        return
    try:
        await bot.start(TOKEN)
    finally:
        if _session is not None:
            await _session.close()

if __name__ == "__main__":
    asyncio.run(_amain())
