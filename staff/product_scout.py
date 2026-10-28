#!/usr/bin/env python3
"""직원 2 · 스카우터: 사서 써보는 쇼츠를 찍었을 때 호기심을 끌 제품을 아마존·테무로 나눠 찾습니다.

제품을 찾는 두 가지 길
1) 자동 발굴: YouTube에서 아마존/테무 제품 시연·하울·리뷰 영상(쇼츠 제외)을 찾아
   제목·설명·챕터에 나온 제품을 AI가 뽑고 평가
2) 직접 담기: 사장님이 아마존·테무 상품 페이지에서 "스카우터에 담기"로 보낸 제품(inbox)을 AI가 평가

모드
  auto    : 정기 근무 (아마존·테무 모두 + 담은 제품 평가)
  more    : "더 찾아줘" 버튼 (지정한 사이트만 추가 발굴 + 담은 제품 평가)
  capture : 담은 제품만 평가 (YouTube 할당량 안 씀)
"""
import argparse
import json
import re
from datetime import timedelta
from urllib.parse import quote_plus

from common import AI, DATA, YouTube, load_json, now_utc, parse_duration, run_staff, save_json, save_report
from video_hunter import is_western, looks_ai

STAFF_ID = "product-scout"
BASE = DATA / STAFF_ID
SITE_NAMES = {"amazon": "아마존", "temu": "테무"}
# 회차마다 돌아가며 사용 → "더 찾아줘"를 누를 때마다 다른 영상이 나옴
SEARCHES = {
    "amazon": {
        "medium": ["amazon gadgets tested", "weird amazon products tested", "amazon finds review", "amazon must haves tested",
                   "testing amazon products", "amazon kitchen gadgets review", "strange amazon products", "amazon home finds tested"],
        "short": ["amazon product demo", "amazon gadget demo", "amazon finds unboxing", "amazon product test"],
    },
    "temu": {
        "medium": ["temu haul testing", "weird temu products tested", "temu gadgets review", "temu finds worth it",
                   "testing temu products", "temu kitchen gadgets", "temu haul honest review", "strange temu products"],
        "short": ["temu product demo", "temu gadget unboxing", "temu product test", "temu finds unboxing"],
    },
}
CHAPTER_RE = re.compile(r"^\s*(?:\d{1,2}:)?\d{1,2}:\d{2}\s*[-–—:|)]?\s*(.{4,80})$", re.MULTILINE)


def norm(s):
    return re.sub(r"[^a-z0-9가-힣]", "", (s or "").lower())[:60]


def links(ko, en):
    ko, en = ko or en, en or ko
    return {
        "coupang": f"https://www.coupang.com/np/search?q={quote_plus(ko)}",
        "amazon": f"https://www.amazon.com/s?k={quote_plus(en)}",
        "temu": f"https://www.temu.com/search_result.html?search_key={quote_plus(en)}",
        "aliexpress": f"https://www.aliexpress.com/wholesale?SearchText={quote_plus(en)}",
    }


def site_from_text(text, default):
    t = text.lower()
    temu, amazon = ("temu" in t), bool(re.search(r"amzn\.to|amazon\.com|amazon finds|\bamazon\b", t))
    if temu and not amazon:
        return "temu"
    if amazon and not temu:
        return "amazon"
    return default


# ---------- 1) YouTube 자동 발굴 ----------

def find_videos(yt, site, rotation, seen_videos, per_site):
    after = (now_utc() - timedelta(days=45)).strftime("%Y-%m-%dT%H:%M:%SZ")
    plan = []
    for duration in ("medium", "short"):
        words = SEARCHES[site][duration]
        plan.append((words[rotation % len(words)], duration, "viewCount" if rotation % 2 == 0 else "relevance"))
    found = {}
    for q, duration, order in plan:
        try:
            data = yt.get("search", cost=100, part="id", q=q, type="video", videoDuration=duration, order=order,
                          publishedAfter=after, regionCode="US", relevanceLanguage="en", maxResults=25)
        except RuntimeError as e:
            print(f"  [건너뜀] {q}: {e}")
            continue
        for item in data.get("items", []):
            found.setdefault(item["id"]["videoId"], q)
    ids = [v for v in found if v not in seen_videos]
    if not ids:
        return [], [p[0] for p in plan]
    videos = yt.batch("videos", ids, "snippet,statistics,contentDetails")
    channels = yt.batch("channels", {v["snippet"]["channelId"] for v in videos.values()}, "snippet")
    out = []
    for vid, v in videos.items():
        sn = v["snippet"]
        sec = parse_duration(v["contentDetails"].get("duration"))
        if sec <= 60 or "#short" in (sn["title"] + sn.get("description", "")[:300]).lower():
            continue  # 쇼츠 제외
        country = channels.get(sn["channelId"], {}).get("snippet", {}).get("country", "")
        if not is_western(country, v) or looks_ai(v):
            continue
        desc = sn.get("description", "")
        out.append({"video_id": vid, "title": sn["title"], "channel": sn["channelTitle"], "seconds": sec,
                    "views": int(v.get("statistics", {}).get("viewCount", 0)), "desc": desc[:1500],
                    "site": site_from_text(sn["title"] + " " + desc, site), "query": found[vid]})
    out.sort(key=lambda x: -x["views"])
    return out[:per_site], [p[0] for p in plan]


EXTRACT_SYSTEM = """너는 한국 쇼츠 리뷰 채널(리뷰하는회사원·소개팁 스타일)의 제품 발굴 담당이야.
아마존/테무 제품을 소개·시연·하울하는 해외 YouTube 영상의 제목, 설명, 챕터를 보고
영상에 나오는 '실물 제품'을 뽑아서, 사장님이 이걸 사서 직접 써보는 쇼츠를 찍었을 때
시청자의 호기심을 얼마나 자극할지 평가해.

호기심이 높은 제품: 처음 보는 신기한 원리/모양, "이게 진짜 돼?" 싶은 기능, 써보는 순간 반전·웃음이 나오는 것,
비교 실험이 되는 것, 1~5만원대로 살 수 있는 것.
낮은 제품: 흔한 생필품(일반 충전기, 텀블러), 써보는 장면이 밋밋한 것, 비싼 가전, 의약품·위험물, 의류 사이즈 문제.

영상당 최대 4개, 호기심 높은 것만. 모든 설명은 한국어로 짧게:
- name_en: 영어 검색어 / name_ko: 쿠팡 검색용 한국어 이름
- curiosity: 0~10 (써보는 쇼츠를 찍었을 때 호기심 자극도)
- wow_ko: 무엇이 신기한지 (35자 이내)
- hook_ko: 첫 3초에 쓸 말 또는 쇼츠 제목 (20자 이내, "~라고?", "진짜 될까?" 같은 호기심형)
- test_ko: 사서 어떻게 써보며 찍을지 (50자 이내)
- reaction_ko: 예상되는 반전/시청자 반응 (35자 이내)
- caution_ko: 주의할 점 (없으면 빈 문자열)
JSON으로만 답해: {"products":[{"video_id":"","name_en":"","name_ko":"","curiosity":0,"wow_ko":"","hook_ko":"","test_ko":"","reaction_ko":"","caution_ko":""}]}"""

CAPTURE_SYSTEM = EXTRACT_SYSTEM.split("영상당 최대 4개")[0].replace(
    "아마존/테무 제품을 소개·시연·하울하는 해외 YouTube 영상의 제목, 설명, 챕터를 보고\n영상에 나오는 '실물 제품'을 뽑아서, ",
    "사장님이 아마존/테무 상품 페이지에서 직접 담아온 제품(상품명, 가격, 사장님 메모) 또는\n"
    "해외 리뷰 영상에서 찾은 제품(found_in_video)을 보고\n") + """각 제품마다 (한국어, 짧게):
- id: 입력의 id 그대로
- name_en: 영어 검색어 / name_ko: 쿠팡 검색용 한국어 이름
- curiosity, wow_ko, hook_ko, test_ko, reaction_ko, caution_ko: 위와 같은 기준
사장님 메모가 있으면 가장 중요한 단서로 써.
JSON으로만 답해: {"products":[{"id":"","name_en":"","name_ko":"","curiosity":0,"wow_ko":"","hook_ko":"","test_ko":"","reaction_ko":"","caution_ko":""}]}"""

FIELDS = ("name_en", "name_ko", "wow_ko", "hook_ko", "test_ko", "reaction_ko", "caution_ko")


def clean(p):
    out = {k: str(p.get(k) or "")[:120] for k in FIELDS}
    try:
        out["curiosity"] = max(0, min(10, int(p.get("curiosity") or 0)))
    except (TypeError, ValueError):
        out["curiosity"] = 0
    return out


def extract_products(ai, videos):
    """영상들 → 제품 목록. AI가 안 되면 챕터/제목에서 이름만 뽑음 (평가 대기 상태)."""
    by_id = {v["video_id"]: v for v in videos}
    results = []
    for i in range(0, len(videos), 5):
        batch = [{k: v[k] for k in ("video_id", "title", "channel", "views", "desc")} for v in videos[i:i + 5]]
        res = ai.ask_json(EXTRACT_SYSTEM, json.dumps(batch, ensure_ascii=False))
        got = [p for p in (res or {}).get("products", []) if isinstance(p, dict) and p.get("video_id") in by_id]
        if got:
            results += [(by_id[p["video_id"]], clean(p), True) for p in got]
            continue
        for v in videos[i:i + 5]:  # 규칙 대체
            names = [m.strip() for m in CHAPTER_RE.findall(v["desc"])
                     if not re.search(r"intro|outro|subscribe|conclusion|final|thoughts", m, re.I)][:4]
            for name in names or [v["title"][:80]]:
                results.append((v, {**{k: "" for k in FIELDS}, "name_en": name, "curiosity": 0}, False))
    return results


# ---------- 2) 직접 담은 제품 ----------

def evaluate_captures(ai, pool):
    """inbox의 새 제품을 목록에 넣고, 아직 평가 안 된 담은 제품을 AI로 평가"""
    inbox_files = sorted((BASE / "inbox").glob("*.json"))
    for f in inbox_files:
        c = load_json(f, None)
        if not isinstance(c, dict) or not c.get("url"):
            f.unlink(missing_ok=True)
            continue
        site = c.get("site") if c.get("site") in SITE_NAMES else site_from_text(c["url"], "amazon")
        pid = f"cap-{norm(c['url'])[-40:]}"
        if pid not in {p["id"] for p in pool}:
            pool.insert(0, {
                "id": pid, "site": site, "source": "capture", "evaluated": False, "added": now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "url": str(c["url"])[:500], "image": str(c.get("image", ""))[:500], "title": str(c.get("title", ""))[:200],
                "price": str(c.get("price", ""))[:40], "memo": str(c.get("memo", ""))[:300], "has_video": bool(c.get("has_video")),
                **{k: "" for k in FIELDS}, "name_en": str(c.get("title", ""))[:120], "curiosity": 0,
            })
        f.unlink(missing_ok=True)

    # 담은 제품 + AI가 안 돼서 평가 못 했던 제품을 (담은 것 먼저) 평가
    todo = sorted((p for p in pool if not p["evaluated"]), key=lambda p: p["source"] != "capture")[:24]
    for i in range(0, len(todo), 8):
        batch = [{"id": p["id"], "site": SITE_NAMES[p["site"]], "title": p.get("title") or p["name_en"],
                  "price": p.get("price", ""), "memo": p.get("memo", ""), "has_product_video": p.get("has_video", True),
                  "found_in_video": (p.get("video") or {}).get("title", "")} for p in todo[i:i + 8]]
        res = ai.ask_json(CAPTURE_SYSTEM, json.dumps(batch, ensure_ascii=False))
        by_id = {p["id"]: p for p in todo}
        for r in (res or {}).get("products", []):
            if isinstance(r, dict) and r.get("id") in by_id:
                by_id[r["id"]].update(clean(r), evaluated=True)
    return len(inbox_files), sum(p["evaluated"] for p in todo)


def work(args):
    ai = AI()
    t = now_utc()
    pool = load_json(BASE / "products.json", [])
    state = load_json(BASE / "state.json", {"rotation": 0, "seen_videos": []})
    known = {p["id"] for p in pool}

    captured, evaluated = evaluate_captures(ai, pool)
    added, quota, searched = [], 0, []

    if args.mode in ("auto", "more"):
        yt = YouTube()
        sites = ["amazon", "temu"] if args.site == "both" else [args.site]
        seen = set(state["seen_videos"])
        for site in sites:
            videos, queries = find_videos(yt, site, state["rotation"], seen, args.per_site)
            searched += [f"{SITE_NAMES[site]}: {q}" for q in queries]
            for v, p, ok in extract_products(ai, videos):
                pid = f"{v['site']}-{norm(p['name_en'] or p['name_ko'])}"
                if pid in known or (ok and p["curiosity"] < args.min_curiosity):
                    continue
                known.add(pid)
                added.append({"id": pid, "site": v["site"], "source": "youtube", "evaluated": ok,
                              "added": t.strftime("%Y-%m-%dT%H:%M:%SZ"), **p,
                              "video": {k: v[k] for k in ("video_id", "title", "channel", "views", "seconds")}})
            seen.update(v["video_id"] for v in videos)
        state["rotation"] += 1
        state["seen_videos"] = list(seen)[-3000:]
        quota = yt.quota

    pool = (added + pool)[:args.pool_size]
    for p in pool:
        p["links"] = links(p["name_ko"], p["name_en"])
    save_json(BASE / "products.json", pool)
    save_json(BASE / "state.json", state)

    by_site = {s: sum(p["site"] == s for p in added) for s in SITE_NAMES}
    summary = {"count": len(added), "new": len(added), "mode": args.mode, "site": args.site, "captured": captured,
               "pool": len(pool), "quota": quota, "searched": searched, "ai": ai.status()}
    save_report(STAFF_ID, {"added": [p["id"] for p in added], **summary}, summary, args.keep)
    parts = [f"{SITE_NAMES[s]} {n}개" for s, n in by_site.items() if args.mode != "capture"]
    msg = ("새 제품 " + ", ".join(parts)) if parts else "담은 제품 평가"
    if captured:
        msg += f" · 담은 제품 {captured}개 접수"
    if evaluated:
        msg += f" · {evaluated}개 평가 완료"
    for p in added[:5]:
        print(f"  [{p['curiosity']}] {SITE_NAMES[p['site']]} | {p['name_ko'] or p['name_en']} | {p['wow_ko']}")
    return {"message": msg, "found": len(added), "ai": ai.status(), "quota": quota}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="직원 2 · 스카우터")
    p.add_argument("--mode", choices=["auto", "more", "capture"], default="auto")
    p.add_argument("--site", choices=["both", "amazon", "temu"], default="both")
    p.add_argument("--per-site", type=int, default=10, help="사이트별로 분석할 영상 수")
    p.add_argument("--min-curiosity", type=int, default=5, help="AI 호기심 점수가 이보다 낮으면 버림")
    p.add_argument("--pool-size", type=int, default=600, help="보관할 제품 수")
    p.add_argument("--keep", type=int, default=60, help="보관할 근무 기록 수")
    a = p.parse_args()
    run_staff(STAFF_ID, lambda: work(a))
