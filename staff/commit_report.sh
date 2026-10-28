#!/usr/bin/env bash
# 직원 보고서(docs/data)를 저장소에 올림. 다른 직원과 동시에 올릴 때를 대비해 최대 5번 재시도.
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add docs/data
git commit -q -m "$1 $(date -u +'%Y-%m-%d %H:%M') UTC" || exit 0
for i in 1 2 3 4 5; do
  git pull -q --rebase -X theirs origin main && git push -q && exit 0
  sleep $((i * 5))
done
exit 1
