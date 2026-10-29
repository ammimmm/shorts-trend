"""임시 점검 2: GitHub Models 응답 헤더·모델 목록 확인. 확인 후 삭제."""
import json, os, subprocess
tok = os.environ.get("GITHUB_TOKEN", "")
def curl(*args):
    r = subprocess.run(["curl", "-sS", "-i", "--max-time", "60", *args], capture_output=True, text=True)
    return (r.stdout + r.stderr)[:1500]
body = json.dumps({"model": "openai/gpt-4.1-mini", "messages": [{"role": "user", "content": "say hi"}], "max_tokens": 20})
out = {
    "catalog": curl("-H", f"Authorization: Bearer {tok}", "https://models.github.ai/catalog/models"),
    "chat": curl("-X", "POST", "-H", f"Authorization: Bearer {tok}", "-H", "Content-Type: application/json",
                 "-H", "Accept: application/vnd.github+json", "-H", "X-GitHub-Api-Version: 2022-11-28",
                 "--data", body, "https://models.github.ai/inference/chat/completions"),
    "chat_no_token": curl("-X", "POST", "-H", "Content-Type: application/json", "--data", body,
                          "https://models.github.ai/inference/chat/completions"),
    "dns": subprocess.run(["getent", "hosts", "models.github.ai"], capture_output=True, text=True).stdout,
}
json.dump(out, open("diag/result.json", "w"), indent=1, ensure_ascii=False)
print(json.dumps(out, indent=1, ensure_ascii=False))
