#!/usr/bin/env python3
"""직원 2 · 스카우터: 리뷰하면 신박하고 재밌을 제품을 골라 쿠팡·아마존·테무 링크와 함께 보고합니다.

재료
1) 헌터가 최근 하루 동안 찾은 해외 히트 영상 속 제품 (조회수·구매의도 댓글 포함)
2) 아마존 Movers & Shakers(급상승 상품) — 아마존이 봇 접속을 막으면 건너뜀
판단
- 무료 AI(GitHub Models)가 신박함·재미·리뷰 적합도를 평가하고 쇼츠 제목·촬영 아이디어를 제안
- AI가 안 되면 영상 데이터만으로 순위를 매김
"""
import argparse
import html
import json
import re
from datetime import datetime, timedelta
from urllib.parse import quote_plus

import requests

from common import AI, CONFIG, DATA, load_json, now_utc, run_staff, save_json, save_report

STAFF_ID = "product-scout"
AMAZON_CATEGORIES = {
    "주방": "https://www.amazon.com/gp/movers-and-shakers/kitchen",
    "생활·집": "https://www.amazon.com/gp/movers-and-shakers/home-garden",
    "장난감": "https://www.amazon.com/gp/movers-and-shakers/toys-and-games",
    "뷰티": "https://www.amazon.com/gp/movers-and-shakers/beauty",
}
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}


def links(ko, en):
    ko, en = ko or en, en or ko
    return {
        "coupang": f"https://www.coupang.com/np/search?q={quote_plus(ko)}",
        "amazon": f"https://www.amazon.com/s?k={quote_plus(en)}",
        "temu": f"https://www.temu.com/search_result.html?search_key={quote_plus(en)}",
        "aliexpress": f"https://www.aliexpress.com/wholesale?SearchText={quote_plus(en)}",
    }


def hunter_products(hours):
    """헌터의 최근 보고서에서 제품 후보 모으기"""
    base = DATA / "video-hunter"
    since = (now_utc() - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    groups = {}
    for entry in load_json(base / "index.json", []):
        if entry["time"] < since:
            break
        for v in load_json(base / entry["file"], {}).get("videos", []):
            name = (v.get("product_en") or "").strip()
            key = re.sub(r"[^a-z0-9 ]", "", name.lower()) or v["video_id"]
            g = groups.setdefault(key, {"product_en": name, "product_ko": v.get("product_ko", ""), "videos": {}, "buy_hits": 0})
            if v["video_id"] not in g["videos"] or v["views"] > g["videos"][v["video_id"]]["views"]:
                g["videos"][v["video_id"]] = {k: v.get(k) for k in ("video_id", "title", "views", "views_per_hour", "style", "summary_ko", "fun_ko")}
            g["buy_hits"] = max(g["buy_hits"], v.get("buy_hits", 0))
    out = []
    for key, g in groups.items():
        vids = sorted(g["videos"].values(), key=lambda x: -x["views"])
        g["videos"] = vids[:3]
        g["key"] = key
        g["best_views"] = vids[0]["views"]
        g["style"] = max(x.get("style") or 0 for x in vids)
        out.append(g)
    return sorted(out, key=lambda g: (-(g["style"] >= 6), -g["best_views"]))


def amazon_movers():
    """아마존 급상승 상품 제목. 아마존은 서버 접속을 자주 막기 때문에 실패해도 정상 진행."""
    items, notes = [], []
    for cat, url in AMAZON_CATEGORIES.items():
        try:
            resp = requests.get(url, headers=BROWSER_HEADERS, timeout=20)
            if resp.status_code != 200 or "captcha" in resp.text.lower()[:5000]:
                notes.append(f"{cat}: 차단({resp.status_code})")
                continue
            titles = re.findall(r'<div class="_cDEzb_p13n-sc-css-line-clamp-\d+_[^"]*">([^<]{8,200})</div>', resp.text)
            if not titles:
                titles = re.findall(r'alt="([^"]{15,200})"', resp.text)
            titles = list(dict.fromkeys(html.unescape(t).strip() for t in titles))[:20]
            items += [{"title": t, "category": cat} for t in titles]
            notes.append(f"{cat}: {len(titles)}개")
        except requests.RequestException as e:
            notes.append(f"{cat}: 실패({type(e).__name__})")
    return items, ", ".join(notes)


AI_SYSTEM = """너는 한국 쇼츠 리뷰 채널(리뷰하는회사원·소개팁 스타일)의 제품 발굴 담당이야.
후보 목록에서 한국 시청자가 "이게 뭐야?" 하고 끝까지 보게 될, 직접 써보며 리뷰하기 좋은 제품을 골라.

좋은 제품: 처음 보는 신박한 아이디어템, 써보는 장면에서 반전/웃음이 나오는 것, 비교 실험이 되는 것,
1~5만원대로 쉽게 살 수 있는 것, 쿠팡·테무·알리에서 구할 수 있을 법한 것.
나쁜 제품: 너무 흔한 것(일반 텀블러, 충전기), 비싼 가전, 써보는 장면이 지루한 것, 의약품·위험물.

각 제품마다 (한국어, 짧게):
- name_ko: 쿠팡에서 검색할 한국어 이름 / name_en: 영어 검색어
- novelty, fun, review_fit: 0~10 (신박함 / 재미 / 쇼츠 리뷰 적합도)
- why_ko: 왜 재밌을지 한 줄 (40자 이내)
- hook_ko: 회사원·소개팁 스타일 쇼츠 제목 예시 (20자 이내, 호기심 유발형)
- test_ko: 어떻게 써보며 찍으면 좋을지 한 줄 (50자 이내)
- source: 후보의 key 값 그대로

같은 제품은 하나로 합치고, 좋은 순서대로 최대 15개. JSON으로만 답해:
{"picks":[{"source":"","name_ko":"","name_en":"","novelty":0,"fun":0,"review_fit":0,"why_ko":"","hook_ko":"","test_ko":""}]}"""


def work(args):
    ai = AI()
    t = now_utc()
    candidates = hunter_products(args.hours)
    amazon, amazon_note = amazon_movers() if not args.no_amazon else ([], "건너뜀")
    print(f"영상 제품 후보 {len(candidates)}개, 아마존 {len(amazon)}개 ({amazon_note})")
    if not candidates and not amazon:
        raise RuntimeError("후보가 없습니다. 헌터 보고서가 아직 없거나 아마존 접속이 막혔습니다.")

    pool = {}
    for g in candidates[:args.video_candidates]:
        pool[g["key"]] = {"key": g["key"], "from": "해외 히트 영상", "name": g["product_en"] or g["videos"][0]["title"][:80],
                          "views": g["best_views"], "buy_comments": g["buy_hits"], "what_happens": g["videos"][0].get("summary_ko", "")}
    for i, a in enumerate(amazon[:args.amazon_candidates]):
        pool[f"amazon{i}"] = {"key": f"amazon{i}", "from": f"아마존 급상승({a['category']})", "name": a["title"][:100]}

    picks = []
    result = ai.ask_json(AI_SYSTEM, "# 후보\n" + json.dumps(list(pool.values()), ensure_ascii=False))
    by_key = {g["key"]: g for g in candidates}
    for p in (result or {}).get("picks", []):
        if not isinstance(p, dict) or p.get("source") not in pool:
            continue
        src = by_key.get(p["source"])
        scores = [max(0, min(10, int(p.get(k) or 0))) for k in ("novelty", "fun", "review_fit")]
        picks.append({
            "name_ko": p.get("name_ko", ""), "name_en": p.get("name_en", ""),
            "novelty": scores[0], "fun": scores[1], "review_fit": scores[2], "score": round(sum(scores) / 3, 1),
            "why_ko": p.get("why_ko", ""), "hook_ko": p.get("hook_ko", ""), "test_ko": p.get("test_ko", ""),
            "from": pool[p["source"]]["from"], "videos": src["videos"] if src else [],
            "buy_hits": src["buy_hits"] if src else 0, "ai": True,
        })
    if not picks:  # AI가 안 되면 영상 데이터 기준으로
        for g in candidates[:15]:
            name = g["product_en"] or g["videos"][0]["title"]
            picks.append({"name_ko": g["product_ko"], "name_en": name, "novelty": 0, "fun": 0, "review_fit": g["style"],
                          "score": g["style"], "why_ko": g["videos"][0].get("fun_ko", ""), "hook_ko": "", "test_ko": "",
                          "from": "해외 히트 영상", "videos": g["videos"], "buy_hits": g["buy_hits"], "ai": False})

    # 전에 추천한 적 없는 제품은 NEW 표시
    seen = load_json(DATA / STAFF_ID / "seen.json", {})
    for p in picks:
        k = re.sub(r"[^a-z0-9]", "", (p["name_en"] or p["name_ko"]).lower())[:60]
        p["new"] = k not in seen
        seen.setdefault(k, t.strftime("%Y-%m-%d"))
        p["links"] = links(p["name_ko"], p["name_en"])
    save_json(DATA / STAFF_ID / "seen.json", seen)

    picks.sort(key=lambda p: (-p["score"], -p["buy_hits"]))
    summary = {"count": len(picks), "new": sum(p["new"] for p in picks), "amazon": amazon_note, "ai": ai.status()}
    save_report(STAFF_ID, {"products": picks, "video_candidates": len(candidates), **summary}, summary, args.keep)
    for p in picks[:5]:
        print(f"  [{p['score']}] {p['name_ko'] or p['name_en']} | {p['why_ko']}")
    return {"message": f"제품 {len(picks)}개 추천 (새 제품 {summary['new']}개)", "found": len(picks), "ai": ai.status(),
            "amazon": amazon_note}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="직원 2 · 스카우터")
    p.add_argument("--hours", type=int, default=24, help="헌터 보고서를 몇 시간치 볼지")
    p.add_argument("--video-candidates", type=int, default=40)
    p.add_argument("--amazon-candidates", type=int, default=40)
    p.add_argument("--no-amazon", action="store_true", help="아마존 확인 건너뛰기")
    p.add_argument("--keep", type=int, default=30, help="보관할 근무 기록 수")
    a = p.parse_args()
    run_staff(STAFF_ID, lambda: work(a))
