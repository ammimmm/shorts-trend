#!/usr/bin/env python3
"""직원 1 · 헌터: 미국·서양권과 일본에서 히트한 '제품 써보기' 영상을 찾아 보고합니다.

수집 분야 (staff/config/hunter_keywords.txt)
  미국 쇼츠 / 일본 쇼츠 / 미국 실사용 리뷰 / 일본 실사용 리뷰
1) 분야별 키워드를 돌아가며 검색 + 지난번에 찾은 좋은 채널들의 새 영상 확인
2) 대상 지역(서양권·일본)이 아닌 영상, AI·CGI 영상, 분야별 길이 초과 영상 제외
3) 리뷰하는회사원·소개팁 스타일과 얼마나 비슷한지 AI가 평가 (무료 GitHub Models)
4) 아웃라이어(조회수÷구독자)를 가장 중요하게, 그다음 스타일·스토리텔링으로 순위를 매겨 보고서 저장
"""
import argparse
import json
import re
from datetime import datetime, timedelta

from common import AI, CONFIG, DATA, YouTube, load_json, now_utc, parse_duration, read_lines, run_staff, save_json, save_report

STAFF_ID = "video-hunter"
WESTERN = {"US", "GB", "CA", "AU", "NZ", "IE"}
TARGET = WESTERN | {"JP"}
GROUPS = {
    # 분야: 검색 지역/언어, 길이, 기간, 근무당 검색 키워드 수, AI 정밀 평가 후보 수, 최대 길이(초)
    "us_shorts": {"label": "미국 쇼츠", "category": "쇼츠", "region": "US", "lang": "en", "duration": "short", "days": 7,
                  "per_run": 3, "candidates": 18, "max_sec": 90},
    "jp_shorts": {"label": "일본 쇼츠", "category": "쇼츠", "region": "JP", "lang": "ja", "duration": "short", "days": 7,
                  "per_run": 2, "candidates": 14, "max_sec": 90},
    "us_review": {"label": "미국 실사용 리뷰", "category": "실사용 리뷰", "region": "US", "lang": "en", "duration": "medium",
                  "days": 21, "per_run": 1, "candidates": 8, "max_sec": 1200},
    "jp_review": {"label": "일본 실사용 리뷰", "category": "실사용 리뷰", "region": "JP", "lang": "ja", "duration": "medium",
                  "days": 21, "per_run": 1, "candidates": 8, "max_sec": 1200},
}

KANA = re.compile(r"[぀-ヿ]")
HAN = re.compile(r"[一-鿿]")
# 대상 지역이 아닌 문자 (키릴, 아랍, 인도계, 태국, 한글)
OTHER_SCRIPT = re.compile(r"[Ѐ-ӿ؀-ۿऀ-෿฀-๿가-힯]")
NON_WESTERN_WORDS = re.compile(
    r"\b(meesho|flipkart|hindi|bhai|kya|nahi|hai|paise|wala|desi|rupees?|rs\.?\s?\d|pakistan|india|indian|bangla|urdu)\b|₹",
    re.IGNORECASE)
AI_WORDS = re.compile(
    r"\bai[\s-]?(generated|video|art|animation|made|created|content)\b|\bmade (with|by|using) ai\b|#ai\b|#aiart|#aivideo|"
    r"#aigenerated|\bsora\b|\bveo\s?\d\b|midjourney|\bkling\b|\brunway(ml)?\b|\bhailuo\b|pika\s?labs|\bcgi\b|#cgi|\bvfx\b|"
    r"#3danimation|AI生成|AI動画|AIで作",
    re.IGNORECASE)
PRODUCT_RE = re.compile(r"gadget|product|amazon|temu|tiktok made me|review|tested|testing|i tried|worth|buy|bought|kitchen|tool|"
                        r"cheap|expensive|\$\d|便利|グッズ|レビュー|使ってみ|検証|買って|100均|ダイソー|商品|アイテム", re.IGNORECASE)
HOOK_RE = re.compile(r"\?|？|\bvs\.?\b|does it|actually|work|tested|testing|i tried|worth it|weird|secret|hack|genius|"
                     r"before you buy|検証|まさか|本当に|すごすぎ|神", re.IGNORECASE)


def is_target_region(country, video):
    """미국·영국 등 서양권 또는 일본 제작자인지"""
    if country:
        return country in TARGET
    sn = video["snippet"]
    title = sn["title"]
    if OTHER_SCRIPT.search(title):
        return False
    if KANA.search(title):
        return True  # 일본어
    if HAN.search(title):
        return False  # 가나 없이 한자만 → 중국어일 가능성
    lang = (sn.get("defaultAudioLanguage") or sn.get("defaultLanguage") or "en").lower()
    return lang[:2] in ("en", "ja") and not NON_WESTERN_WORDS.search(title + " " + sn.get("description", "")[:300])


is_western = is_target_region  # 스카우터에서 사용


def looks_ai(video):
    sn = video["snippet"]
    return bool(AI_WORDS.search(" ".join([sn["title"], sn.get("description", "")[:500], " ".join(sn.get("tags", []))])))


def rule_style(r):
    """AI가 없을 때 쓰는 간이 스타일 점수 (0~10)"""
    if not PRODUCT_RE.search(r["title"] + " " + " ".join(r["tags"])):
        return 3  # 제품 단서가 없으면 낮게
    s = 5
    if GROUPS[r["group"]]["category"] == "쇼츠":
        s += 2 if 25 <= r["seconds"] <= 65 else -1
    s += 2 if HOOK_RE.search(r["title"]) else 0
    s += 1 if r["country"] in TARGET else 0
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
아래 '기준 스타일'과 각 영상의 메타데이터(분야, 제목, 설명, 태그, 길이, 조회수, 구독자)를 보고 판단해.
영상은 미국·서양권 또는 일본 영상이야 (일본어 제목도 정상). 영상 화면은 볼 수 없으니 텍스트 단서로 추론하고, 모르면 보수적으로 판단해.

판단 항목:
- ai_generated: AI 생성/CGI/합성으로 보이면 true
- off_region: 인도·파키스탄·중국 등 서양권/일본이 아닌 제작자로 보이면 true (힌글리시, 현지 쇼핑몰, 루피 등)
- style_match: 0~10. 실제 사람이 실물 제품을 직접 써보고 반응/반전/유머가 있을수록 높게.
  '실사용 리뷰' 분야는 실제로 써본 뒤 솔직하게 장단점을 보여줄수록 높게.
  제품이 없거나, 단순 광고, 짜깁기, 요리만, 몰카/춤이면 3 이하.
- story: 0~10. 단순 제품 소개가 아니라 '이야기'가 있을수록 높게 (기준 스타일의 스토리텔링 패턴 참고:
  상황 설정·캐릭터, 기대→반전, 질문→검증, 제품의 뒷이야기, 생활 문제→해결, 시리즈)
- story_ko: 이 영상의 스토리 흐름 한 줄 (한국어, 50자 이내, 예: "광고 보고 산 만능열쇠 → 자물쇠 실험 → 하나도 못 땀")
- product_en / product_ko: 영상 속 핵심 제품명 (모르면 빈 문자열). product_ko는 쿠팡에서 검색할 한국어 이름
- summary_ko: 영상에서 무슨 일이 일어나는지 한 줄 (한국어, 40자 이내)
- fun_ko: 시청자가 재밌어하는/끝까지 보는 포인트 한 줄 (한국어, 40자 이내)

JSON으로만 답해: {"results":[{"id":"...","ai_generated":false,"off_region":false,"style_match":7,"story":6,"story_ko":"","product_en":"","product_ko":"","summary_ko":"","fun_ko":""}]}"""


def ai_review(ai, rows):
    style = (CONFIG / "reference_style.md").read_text(encoding="utf-8")
    verdicts = {}
    for i in range(0, len(rows), 15):
        items = [{"id": r["video_id"], "field": GROUPS[r["group"]]["label"], "title": r["title"], "channel": r["channel"],
                  "seconds": r["seconds"], "views": r["views"], "subs": r["subs"], "desc": r["desc"][:200], "tags": r["tags"][:8]}
                 for r in rows[i:i + 15]]
        result = ai.ask_json(AI_SYSTEM, f"# 기준 스타일\n{style}\n\n# 영상 목록\n{json.dumps(items, ensure_ascii=False)}")
        for v in (result or {}).get("results", []):
            if isinstance(v, dict) and v.get("id"):
                verdicts[v["id"]] = v
    return verdicts


def load_keywords():
    kws = {g: [] for g in GROUPS}
    for line in read_lines(CONFIG / "hunter_keywords.txt"):
        group, _, kw = line.partition("|")
        if group.strip() in kws and kw.strip():
            kws[group.strip()].append(kw.strip())
    return kws


def work(args):
    yt, ai = YouTube(), AI()
    t = now_utc()
    state = load_json(DATA / STAFF_ID / "state.json", {"rotation": 0})
    rot = state["rotation"]
    found = {}  # video_id -> {"group", "kw": set}

    for group, words in load_keywords().items():
        g = GROUPS[group]
        after = (t - timedelta(days=g["days"])).strftime("%Y-%m-%dT%H:%M:%SZ")
        for i in range(min(g["per_run"], len(words))):
            kw = words[(rot * g["per_run"] + i) % len(words)]
            try:
                data = yt.get("search", cost=100, part="id", q=kw, type="video", videoDuration=g["duration"], order="viewCount",
                              publishedAfter=after, regionCode=g["region"], relevanceLanguage=g["lang"], maxResults=50)
            except RuntimeError as e:
                print(f"  [건너뜀] {kw}: {e}")
                continue
            for item in data.get("items", []):
                found.setdefault(item["id"]["videoId"], {"group": group, "kw": set()})["kw"].add(kw)

    # 지난번에 찾은 '스타일 맞는' 채널들의 최근 업로드 확인 (채널당 1유닛)
    tracked = load_json(DATA / STAFF_ID / "channels.json", [])
    recent = (t - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for ch in tracked[:args.track]:
        try:
            data = yt.get("playlistItems", part="contentDetails", playlistId="UU" + ch["id"][2:], maxResults=10)
        except RuntimeError:
            continue
        for item in data.get("items", []):
            if item["contentDetails"].get("videoPublishedAt", "") >= recent:
                found.setdefault(item["contentDetails"]["videoId"], {"group": ch.get("group", "us_shorts"), "kw": set()})["kw"].add("단골 채널")

    if not found:
        raise RuntimeError("검색 결과가 없습니다 (할당량 초과 가능성)")

    videos = yt.batch("videos", found, "snippet,statistics,contentDetails")
    channels = yt.batch("channels", {v["snippet"]["channelId"] for v in videos.values()}, "snippet,statistics")

    counts = {"수집": len(videos), "길이 초과": 0, "대상 지역 아님": 0, "AI 영상": 0}
    rows = []
    for vid, v in videos.items():
        group = found[vid]["group"]
        sec = parse_duration(v["contentDetails"].get("duration"))
        if not sec or sec > GROUPS[group]["max_sec"]:
            counts["길이 초과"] += 1
            continue
        ch = channels.get(v["snippet"]["channelId"], {})
        country = ch.get("snippet", {}).get("country", "")
        if not is_target_region(country, v):
            counts["대상 지역 아님"] += 1
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
            "channel_id": v["snippet"]["channelId"], "country": country, "group": group,
            "field": GROUPS[group]["label"], "category": GROUPS[group]["category"], "region": GROUPS[group]["region"],
            "keywords": ", ".join(sorted(found[vid]["kw"])), "published": published.strftime("%Y-%m-%d %H:%M"),
            "seconds": sec, "views": views, "likes": likes, "comments": int(st.get("commentCount", 0)), "subs": subs,
            "outlier": round(views / max(subs, 100), 1),
            "views_per_hour": round(views / max((t - published).total_seconds() / 3600, 1)),
            "like_rate": round(likes / max(views, 1) * 100, 2),
            "desc": v["snippet"].get("description", ""), "tags": v["snippet"].get("tags", []),
        })

    # 1차 순위(아웃라이어 중심) → 분야별 상위 후보만 AI 정밀 평가 (무료 AI 한도 절약)
    rank(rows, [("outlier", 0.6), ("views_per_hour", 0.3), ("like_rate", 0.1)])
    rows.sort(key=lambda r: -r["score"])
    candidates = []
    for group, g in GROUPS.items():
        candidates += [r for r in rows if r["group"] == group][:g["candidates"]]

    verdicts = ai_review(ai, candidates)
    final = []
    for r in candidates:
        v = verdicts.get(r["video_id"])
        if v and v.get("ai_generated"):
            counts["AI 영상"] += 1
            continue
        if v and (v.get("off_region") or v.get("non_western")):
            counts["대상 지역 아님"] += 1
            continue
        r["ai_checked"] = bool(v)
        try:
            r["style"] = max(0, min(10, int(v.get("style_match", 0)))) if v else rule_style(r)
        except (TypeError, ValueError):
            r["style"] = rule_style(r)
        try:
            r["story"] = max(0, min(10, int(v.get("story", 0)))) if v else (5 if HOOK_RE.search(r["title"]) else 3)
        except (TypeError, ValueError):
            r["story"] = 3
        for k in ("product_en", "product_ko", "summary_ko", "fun_ko", "story_ko"):
            r[k] = str((v or {}).get(k, "") or "")
        final.append(r)

    # 최종 순위: 아웃라이어가 가장 중요, 그다음 스타일·스토리
    rank(final, [("outlier", 0.45), ("style", 0.2), ("story", 0.15), ("views_per_hour", 0.1), ("like_rate", 0.1)])
    final.sort(key=lambda r: -r["score"])
    final = [r for r in final if r["style"] >= args.min_style] or final

    # AI가 확인한 스타일 맞는 채널만 '단골 채널'로 기억해서 다음 근무 때 새 영상을 바로 확인
    by_id = {c["id"]: c for c in tracked}
    for r in final:
        if r["ai_checked"] and r["style"] >= 7 and r["outlier"] >= 3:
            c = by_id.setdefault(r["channel_id"], {"id": r["channel_id"], "name": r["channel"], "hits": 0})
            c.update(hits=c["hits"] + 1, last=t.strftime("%Y-%m-%d"), group=r["group"])
    save_json(DATA / STAFF_ID / "channels.json", sorted(by_id.values(), key=lambda c: (-c["hits"], c.get("last", "")))[:40], indent=1)
    state["rotation"] = rot + 1
    save_json(DATA / STAFF_ID / "state.json", state)

    keep = ["video_id", "title", "channel", "country", "group", "field", "category", "region", "keywords", "published",
            "seconds", "views", "likes", "comments", "subs", "outlier", "views_per_hour", "like_rate", "style", "story", "ai_checked",
            "product_en", "product_ko", "summary_ko", "fun_ko", "story_ko", "score"]
    report_rows = [{k: r[k] for k in keep} for r in final[:args.top]]
    by_field = {g["label"]: sum(r["group"] == k for r in report_rows) for k, g in GROUPS.items()}
    summary = {"count": len(report_rows), "excluded": counts, "fields": by_field, "quota": yt.quota, "ai": ai.status()}
    save_report(STAFF_ID, {"days": GROUPS["us_shorts"]["days"], "videos": report_rows, **summary}, summary, args.keep)
    print(f"할당량 약 {yt.quota}, AI 호출 {ai.calls}회, 보고 {len(report_rows)}개 {by_field}, 제외 {counts}")
    for r in report_rows[:8]:
        print(f"  [{r['score']:5.1f}] x{r['outlier']:<7} {r['field']} | {r['views']:>10,}회 | {r['title'][:50]}")
    return {"message": f"영상 {len(report_rows)}개 보고 (" + ", ".join(f"{k} {v}" for k, v in by_field.items()) + ")",
            "found": len(report_rows), "quota": yt.quota, "ai": ai.status(), "excluded": counts}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="직원 1 · 헌터")
    p.add_argument("--min-style", type=int, default=4, help="스타일 점수가 이보다 낮으면 보고에서 제외")
    p.add_argument("--track", type=int, default=15, help="새 영상을 확인할 단골 채널 수")
    p.add_argument("--top", type=int, default=80, help="보고할 영상 수")
    p.add_argument("--keep", type=int, default=50, help="보관할 근무 기록 수 (약 1주)")
    a = p.parse_args()
    run_staff(STAFF_ID, lambda: work(a))
