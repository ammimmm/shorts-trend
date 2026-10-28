#!/usr/bin/env python3
"""직원 1 · 헌터: 서양권에서 히트한 '제품 써보기' 쇼츠를 찾아 보고합니다.

1) 키워드 검색 + 지난번에 찾은 좋은 채널들의 새 영상 확인
2) 서양권이 아닌 영상, AI·CGI 영상, 너무 긴 영상 제외
3) 리뷰하는회사원·소개팁 스타일과 얼마나 비슷한지 AI가 평가 (무료 GitHub Models)
4) 속도·아웃라이어·구매의도·스타일 점수로 순위를 매겨 보고서 저장
"""
import argparse
import json
import re
from datetime import datetime, timedelta

from common import AI, CONFIG, DATA, YouTube, load_json, now_utc, parse_duration, read_lines, run_staff, save_json, save_report

STAFF_ID = "video-hunter"
WESTERN = {"US", "GB", "CA", "AU", "NZ", "IE"}

# 서양권이 아닌 문자 (데바나가리, 아랍, 벵골, 타밀, 태국, 한중일, 키릴 등)
NON_LATIN = re.compile(r"[Ѐ-ӿ؀-ۿऀ-෿฀-๿぀-ヿ一-鿿가-힯]")
NON_WESTERN_WORDS = re.compile(
    r"\b(meesho|flipkart|hindi|bhai|kya|nahi|hai|paise|wala|desi|rupees?|rs\.?\s?\d|pakistan|india|indian|bangla|urdu)\b|₹",
    re.IGNORECASE)
AI_WORDS = re.compile(
    r"\bai[\s-]?(generated|video|art|animation|made|created|content)\b|\bmade (with|by|using) ai\b|#ai\b|#aiart|#aivideo|"
    r"#aigenerated|\bsora\b|\bveo\s?\d\b|midjourney|\bkling\b|\brunway(ml)?\b|\bhailuo\b|pika\s?labs|\bcgi\b|#cgi|\bvfx\b|#3danimation",
    re.IGNORECASE)
BUY_RE = re.compile(
    r"where (can|do|did) (i|you) (buy|get|find)|\blink\b|name of (the|this) product|what('?s| is) (it|this|the product) called|"
    r"need (this|one|it)|want (this|one)|just (bought|ordered)|add(ed)? to (my )?cart|take my money|\bbuying\b|where.*from",
    re.IGNORECASE)
PRODUCT_RE = re.compile(r"gadget|product|amazon|temu|tiktok made me|review|tested|testing|i tried|worth|buy|bought|kitchen|tool|"
                        r"cheap|expensive|\$\d", re.IGNORECASE)
HOOK_RE = re.compile(r"\?|\bvs\.?\b|does it|actually|work|tested|testing|i tried|worth it|weird|secret|hack|genius|before you buy",
                     re.IGNORECASE)


def is_western(country, video):
    if country:
        return country in WESTERN
    sn = video["snippet"]
    lang = (sn.get("defaultAudioLanguage") or sn.get("defaultLanguage") or "en").lower()
    text = sn["title"] + " " + sn.get("description", "")[:300]
    return lang.startswith("en") and not NON_LATIN.search(sn["title"]) and not NON_WESTERN_WORDS.search(text)


def looks_ai(video):
    sn = video["snippet"]
    return bool(AI_WORDS.search(" ".join([sn["title"], sn.get("description", "")[:500], " ".join(sn.get("tags", []))])))


def rule_style(r):
    """AI가 없을 때 쓰는 간이 스타일 점수 (0~10)"""
    if not PRODUCT_RE.search(r["title"] + " " + " ".join(r["tags"])):
        return 3  # 제품 단서가 없으면 낮게
    s = 4
    s += 2 if 25 <= r["seconds"] <= 65 else -1
    s += 2 if HOOK_RE.search(r["title"]) else 0
    s += 1 if r["buy_hits"] >= 2 else 0
    s += 1 if r["country"] in WESTERN else 0
    return max(0, min(10, s))


def rank(rows, fields):
    n = len(rows)
    for r in rows:
        r["score"] = 0.0
    for field, weight in fields:
        for i, r in enumerate(sorted(rows, key=lambda x: x[field])):
            r["score"] += weight * (i / max(n - 1, 1)) * 100
    for r in rows:
        r["score"] = round(r["score"], 1)


AI_SYSTEM = """너는 한국 쇼츠 크리에이터를 돕는 해외 영상 리서처야.
아래 '기준 스타일'과 각 영상의 메타데이터(제목, 설명, 태그, 인기 댓글, 길이, 조회수)를 보고 판단해.
영상 화면은 볼 수 없으니 텍스트 단서로 최대한 추론하고, 모르면 보수적으로 판단해.

판단 항목:
- ai_generated: AI 생성/CGI/합성으로 보이면 true
- non_western: 인도·파키스탄 등 비서양권 제작자로 보이면 true (힌글리시, 현지 쇼핑몰, 루피 등)
- style_match: 0~10. 실제 사람이 실물 제품을 직접 써보고 반응/반전/유머가 있는 '제품 써보기' 쇼츠일수록 높게.
  제품이 없거나, 단순 광고, 짜깁기, 요리만, 몰카/춤이면 3 이하.
- product_en / product_ko: 영상 속 핵심 제품명 (모르면 빈 문자열). product_ko는 쿠팡에서 검색할 한국어 이름
- summary_ko: 영상에서 무슨 일이 일어나는지 한 줄 (한국어, 40자 이내)
- fun_ko: 시청자가 재밌어하는/끝까지 보는 포인트 한 줄 (한국어, 40자 이내)

JSON으로만 답해: {"results":[{"id":"...","ai_generated":false,"non_western":false,"style_match":7,"product_en":"","product_ko":"","summary_ko":"","fun_ko":""}]}"""


def ai_review(ai, rows, comments):
    style = (CONFIG / "reference_style.md").read_text(encoding="utf-8")
    verdicts = {}
    for i in range(0, len(rows), 15):
        items = []
        for r in rows[i:i + 15]:
            items.append({
                "id": r["video_id"], "title": r["title"], "channel": r["channel"], "seconds": r["seconds"],
                "views": r["views"], "subs": r["subs"], "desc": r["desc"][:200], "tags": r["tags"][:8],
                "top_comments": [c[:100] for c in comments.get(r["video_id"], [])[:3]],
            })
        result = ai.ask_json(AI_SYSTEM, f"# 기준 스타일\n{style}\n\n# 영상 목록\n{json.dumps(items, ensure_ascii=False)}")
        for v in (result or {}).get("results", []):
            if isinstance(v, dict) and v.get("id"):
                verdicts[v["id"]] = v
    return verdicts


def work(args):
    yt, ai = YouTube(), AI()
    t = now_utc()
    published_after = (t - timedelta(days=args.days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    found = {}  # video_id -> set(출처)

    for kw in read_lines(CONFIG / "hunter_keywords.txt"):
        try:
            data = yt.get("search", cost=100, part="id", q=kw, type="video", videoDuration="short", order="viewCount",
                          publishedAfter=published_after, regionCode="US", relevanceLanguage="en", maxResults=50)
        except RuntimeError as e:
            print(f"  [건너뜀] {kw}: {e}")
            if "quotaExceeded" in str(e):
                break
            continue
        for item in data.get("items", []):
            found.setdefault(item["id"]["videoId"], set()).add(kw)

    # 지난번에 찾은 '스타일 맞는' 채널들의 최근 업로드 확인 (채널당 1유닛)
    tracked = load_json(DATA / STAFF_ID / "channels.json", [])
    for ch in tracked[:args.track]:
        try:
            data = yt.get("playlistItems", part="contentDetails", playlistId="UU" + ch["id"][2:], maxResults=10)
        except RuntimeError:
            continue
        for item in data.get("items", []):
            if item["contentDetails"].get("videoPublishedAt", "") >= published_after:
                found.setdefault(item["contentDetails"]["videoId"], set()).add("단골 채널")

    if not found:
        raise RuntimeError("검색 결과가 없습니다 (할당량 초과 가능성)")

    videos = yt.batch("videos", found, "snippet,statistics,contentDetails")
    channels = yt.batch("channels", {v["snippet"]["channelId"] for v in videos.values()}, "snippet,statistics")

    counts = {"수집": len(videos), "길이 초과": 0, "비서양권": 0, "AI 영상": 0}
    rows = []
    for vid, v in videos.items():
        sec = parse_duration(v["contentDetails"].get("duration"))
        if not sec or sec > args.max_seconds:
            counts["길이 초과"] += 1
            continue
        ch = channels.get(v["snippet"]["channelId"], {})
        country = ch.get("snippet", {}).get("country", "")
        if not is_western(country, v):
            counts["비서양권"] += 1
            continue
        if looks_ai(v):
            counts["AI 영상"] += 1
            continue
        st, cst = v.get("statistics", {}), ch.get("statistics", {})
        views, likes = int(st.get("viewCount", 0)), int(st.get("likeCount", 0))
        subs = 0 if cst.get("hiddenSubscriberCount") else int(cst.get("subscriberCount", 0))
        published = datetime.fromisoformat(v["snippet"]["publishedAt"].replace("Z", "+00:00"))
        rows.append({
            "video_id": vid, "title": v["snippet"]["title"], "channel": v["snippet"]["channelTitle"],
            "channel_id": v["snippet"]["channelId"], "country": country, "keywords": ", ".join(sorted(found[vid])),
            "published": published.strftime("%Y-%m-%d %H:%M"), "seconds": sec, "views": views, "likes": likes,
            "comments": int(st.get("commentCount", 0)), "subs": subs,
            "outlier": round(views / max(subs, 100), 1),
            "views_per_hour": round(views / max((t - published).total_seconds() / 3600, 1)),
            "like_rate": round(likes / max(views, 1) * 100, 2),
            "buy_hits": 0, "buy_examples": "", "desc": v["snippet"].get("description", ""), "tags": v["snippet"].get("tags", []),
        })

    # 1차 순위 → 상위 후보만 댓글 확인 + AI 평가 (할당량·무료 AI 한도 절약)
    rank(rows, [("views_per_hour", 0.45), ("outlier", 0.35), ("like_rate", 0.2)])
    rows.sort(key=lambda r: -r["score"])
    candidates = rows[:args.candidates]
    comments = {}
    for r in candidates:
        try:
            data = yt.get("commentThreads", part="snippet", videoId=r["video_id"], order="relevance", maxResults=50,
                          textFormat="plainText")
        except RuntimeError:
            continue
        texts = [i["snippet"]["topLevelComment"]["snippet"]["textDisplay"].replace("\n", " ") for i in data.get("items", [])]
        comments[r["video_id"]] = texts
        hits = [x for x in texts if BUY_RE.search(x)]
        r["buy_hits"], r["buy_examples"] = len(hits), " | ".join(h[:120] for h in hits[:3])

    verdicts = ai_review(ai, candidates, comments)
    final = []
    for r in candidates:
        v = verdicts.get(r["video_id"])
        if v and v.get("ai_generated"):
            counts["AI 영상"] += 1
            continue
        if v and v.get("non_western"):
            counts["비서양권"] += 1
            continue
        r["ai_checked"] = bool(v)
        r["style"] = max(0, min(10, int(v.get("style_match", 0)))) if v else rule_style(r)
        for k in ("product_en", "product_ko", "summary_ko", "fun_ko"):
            r[k] = (v or {}).get(k, "") or ""
        final.append(r)

    rank(final, [("style", 0.35), ("views_per_hour", 0.25), ("outlier", 0.2), ("buy_hits", 0.1), ("like_rate", 0.1)])
    final.sort(key=lambda r: -r["score"])
    final = [r for r in final if r["style"] >= args.min_style] or final

    # AI가 확인한 스타일 맞는 채널만 '단골 채널'로 기억해서 다음 근무 때 새 영상을 바로 확인
    by_id = {c["id"]: c for c in tracked}
    for r in final:
        if r["ai_checked"] and r["style"] >= 7 and r["outlier"] >= 2:
            c = by_id.setdefault(r["channel_id"], {"id": r["channel_id"], "name": r["channel"], "hits": 0})
            c["hits"] += 1
            c["last"] = t.strftime("%Y-%m-%d")
    save_json(DATA / STAFF_ID / "channels.json", sorted(by_id.values(), key=lambda c: (-c["hits"], c.get("last", "")))[:40], indent=1)

    keep = ["video_id", "title", "channel", "country", "keywords", "published", "seconds", "views", "likes", "comments",
            "subs", "outlier", "views_per_hour", "like_rate", "buy_hits", "buy_examples", "style", "ai_checked",
            "product_en", "product_ko", "summary_ko", "fun_ko", "score"]
    report_rows = [{k: r[k] for k in keep} for r in final[:args.top]]
    summary = {"count": len(report_rows), "excluded": counts, "quota": yt.quota, "ai": ai.status()}
    save_report(STAFF_ID, {"days": args.days, "videos": report_rows, **summary}, summary, args.keep)
    print(f"할당량 약 {yt.quota}, AI 호출 {ai.calls}회, 보고 {len(report_rows)}개, 제외 {counts}")
    for r in report_rows[:5]:
        print(f"  [{r['score']:5.1f}] 스타일 {r['style']} | {r['views']:>10,}회 | {r['title'][:50]}")
    return {"message": f"영상 {len(report_rows)}개 보고", "found": len(report_rows), "quota": yt.quota, "ai": ai.status(),
            "excluded": counts}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="직원 1 · 헌터")
    p.add_argument("--days", type=int, default=7, help="최근 며칠 이내 업로드")
    p.add_argument("--max-seconds", type=int, default=90, help="이 길이(초) 이하만")
    p.add_argument("--candidates", type=int, default=45, help="댓글·AI로 정밀 평가할 후보 수")
    p.add_argument("--min-style", type=int, default=4, help="스타일 점수가 이보다 낮으면 보고에서 제외")
    p.add_argument("--track", type=int, default=15, help="새 영상을 확인할 단골 채널 수")
    p.add_argument("--top", type=int, default=60, help="보고할 영상 수")
    p.add_argument("--keep", type=int, default=70, help="보관할 근무 기록 수 (약 1주)")
    a = p.parse_args()
    run_staff(STAFF_ID, lambda: work(a))
