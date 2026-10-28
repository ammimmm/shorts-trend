"""직원들이 같이 쓰는 도구: YouTube API, 무료 AI(GitHub Models), 보고서 저장."""
import json
import os
import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "data"
CONFIG = Path(__file__).resolve().parent / "config"
YT_API = "https://www.googleapis.com/youtube/v3"
AI_URL = "https://models.github.ai/inference/chat/completions"
AI_MODEL = os.environ.get("AI_MODEL", "openai/gpt-4.1-mini")


def now_utc():
    return datetime.now(timezone.utc)


def read_lines(path):
    return [l.strip() for l in Path(path).read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.strip().startswith("#")]


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return default


def save_json(path, obj, indent=None):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=indent), encoding="utf-8")


# ---------- YouTube ----------

class YouTube:
    def __init__(self):
        self.key = os.environ.get("YOUTUBE_API_KEY", "").strip()
        if not self.key:
            raise RuntimeError("YOUTUBE_API_KEY가 없습니다. 저장소 Settings > Secrets에 등록하세요.")
        self.quota = 0

    def get(self, endpoint, cost=1, **params):
        resp = requests.get(f"{YT_API}/{endpoint}", params={**params, "key": self.key}, timeout=30)
        self.quota += cost
        if resp.status_code != 200:
            try:
                reason = resp.json()["error"]["errors"][0]["reason"]
            except (ValueError, KeyError, IndexError):
                reason = resp.text[:200]
            raise RuntimeError(f"{endpoint} 실패 ({resp.status_code}): {reason}")
        return resp.json()

    def batch(self, endpoint, ids, part):
        out = {}
        ids = list(ids)
        for i in range(0, len(ids), 50):
            data = self.get(endpoint, part=part, id=",".join(ids[i:i + 50]))
            out.update({item["id"]: item for item in data.get("items", [])})
        return out


def parse_duration(iso):
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


# ---------- 무료 AI (GitHub Models) ----------

class AI:
    """GitHub Actions의 GITHUB_TOKEN으로 GitHub Models를 무료로 호출. 실패하면 None을 돌려주고 규칙 기반으로 진행."""

    def __init__(self):
        self.token = os.environ.get("GITHUB_TOKEN", "").strip()
        self.calls = 0
        self.errors = []

    @property
    def enabled(self):
        return bool(self.token) and os.environ.get("AI_DISABLED") != "1"

    def ask_json(self, system, user, max_tokens=4000):
        if not self.enabled:
            return None
        try:
            resp = requests.post(AI_URL, timeout=120, headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
            }, json={
                "model": AI_MODEL,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "response_format": {"type": "json_object"},
                "max_tokens": max_tokens,
            })
            self.calls += 1
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            text = resp.json()["choices"][0]["message"]["content"]
            return json.loads(text[text.find("{"):text.rfind("}") + 1])
        except Exception as e:  # AI가 안 돼도 직원은 규칙으로 계속 일함
            self.errors.append(str(e)[:200])
            print(f"  [AI 실패] {e}")
            return None

    def status(self):
        if not self.enabled:
            return "미사용"
        if self.calls and not self.errors:
            return f"사용 ({AI_MODEL})"
        if self.errors and len(self.errors) >= max(self.calls, 1):
            return f"실패 → 규칙으로 대체 ({self.errors[-1][:80]})"
        return f"일부 실패 ({len(self.errors)}건)"


# ---------- 업무 보고 ----------

def save_report(staff_id, report, summary, keep):
    """docs/data/<직원>/runs/<시각>.json 저장, index.json에 근무 일지 추가 (최근 keep회만 보관)"""
    base = DATA / staff_id
    t = now_utc()
    stamp = t.strftime("%Y%m%d_%H%M")
    report = {"time": t.strftime("%Y-%m-%dT%H:%M:%SZ"), **report}
    save_json(base / "runs" / f"{stamp}.json", report)
    index = [e for e in load_json(base / "index.json", []) if e["file"] != f"runs/{stamp}.json"]
    index.insert(0, {"file": f"runs/{stamp}.json", "time": report["time"], **summary})
    for old in index[keep:]:
        (base / old["file"]).unlink(missing_ok=True)
    save_json(base / "index.json", index[:keep], indent=1)


def run_staff(staff_id, work):
    """직원 업무 실행 + 결과(성공/실패)를 status.json에 기록. 대시보드가 이걸 읽어 근무 상태를 표시."""
    started = now_utc()
    status = {"time": started.strftime("%Y-%m-%dT%H:%M:%SZ")}
    try:
        result = work() or {}
        status.update(ok=True, message=result.pop("message", "정상 완료"), **result)
        code = 0
    except Exception as e:
        traceback.print_exc()
        status.update(ok=False, message=f"오류: {str(e)[:300]}")
        code = 1
    status["seconds"] = round((now_utc() - started).total_seconds())
    save_json(DATA / staff_id / "status.json", status, indent=1)
    print(json.dumps(status, ensure_ascii=False, indent=1))
    sys.exit(code)
