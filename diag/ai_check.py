"""임시 점검: GitHub Models(무료 AI) 호출 방식별 응답 확인. 확인 후 삭제."""
import json, os, requests
tok = os.environ.get("GITHUB_TOKEN", "")
body = {"model": "openai/gpt-4.1-mini", "messages": [{"role": "user", "content": 'Reply with JSON {"ok": true}'}], "max_tokens": 30}
tries = {
    "A models.github.ai vnd": ("https://models.github.ai/inference/chat/completions", {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}),
    "B models.github.ai json": ("https://models.github.ai/inference/chat/completions", {"Accept": "application/json"}),
    "C azure endpoint": ("https://models.inference.ai.azure.com/chat/completions", {"Accept": "application/json"}),
}
out = {"token_len": len(tok), "proxy_env": {k: bool(os.environ.get(k)) for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY")}}
for name, (url, extra) in tries.items():
    for redirects in (False, True):
        b = dict(body, model="gpt-4o-mini") if "azure" in url else body
        try:
            r = requests.post(url, json=b, timeout=60, allow_redirects=redirects,
                              headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json", **extra})
            out[f"{name} redirects={redirects}"] = {"status": r.status_code, "ctype": r.headers.get("content-type"),
                "location": r.headers.get("location"), "history": [h.status_code for h in r.history], "body": r.text[:300]}
        except Exception as e:
            out[f"{name} redirects={redirects}"] = {"error": str(e)[:300]}
json.dump(out, open("diag/result.json", "w"), indent=1, ensure_ascii=False)
print(json.dumps(out, indent=1, ensure_ascii=False))
