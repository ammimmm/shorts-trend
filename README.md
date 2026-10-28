# 내 쇼츠 스튜디오: 직원 관리

홈페이지: **https://ammimmm.github.io/shorts-trend/**

GitHub Actions에서 자동으로 일하는 "직원"들을 홈페이지에서 관리합니다. 서버도 비용도 필요 없습니다.

| 직원 | 하는 일 | 근무 |
|---|---|---|
| 🎬 헌터 | 서양권에서 터진 '제품 써보기' 쇼츠를 찾음. AI 영상과 비서양권 영상은 제외하고, 리뷰하는회사원·소개팁 스타일과 비슷한 순으로 보고 | 하루 10회 |
| 🛒 스카우터 | 헌터가 찾은 영상과 아마존 급상승 상품에서 리뷰하면 재밌을 제품을 고름. 쇼츠 제목, 촬영 아이디어, 쿠팡·아마존·테무·알리 링크를 함께 보고 | 하루 2회 |

- 판단(AI 영상 여부, 스타일 일치도, 제품명, 추천 이유)은 **GitHub Models 무료 AI**가 합니다. AI가 안 되면 규칙 기반으로 계속 일합니다.
- 기준 스타일 분석: `staff/config/reference_style.md`

## 일 시키기
- 자동: 정해진 시간마다 알아서 근무합니다.
- 수동: 홈페이지 직원 카드의 **지금 일 시키기**를 누르고, GitHub 화면에서 **Run workflow**를 누릅니다.

## 설정 바꾸기 (GitHub 웹에서 파일을 열고 연필 아이콘으로 수정)
| 바꿀 것 | 파일 |
|---|---|
| 헌터 검색 키워드 (1개당 할당량 100, 8개가 적정) | `staff/config/hunter_keywords.txt` |
| AI가 참고하는 기준 스타일 | `staff/config/reference_style.md` |
| 근무 시간 | `.github/workflows/video-hunter.yml`, `product-scout.yml`의 `cron` (UTC 기준 = 한국시간 - 9) |
| 홈페이지의 직원 이름과 설명, 채용 예정 목록 | `docs/data/staff.json` |

## 구조 (직원을 늘릴 때)
```
staff/common.py          직원 공용 도구 (YouTube, 무료 AI, 보고서 저장)
staff/video_hunter.py    직원1 헌터
staff/product_scout.py   직원2 스카우터
.github/workflows/       직원별 근무 일정
docs/index.html          직원 관리 홈페이지
docs/data/staff.json     직원 명단
docs/data/<직원id>/      직원별 상태(status.json), 근무 일지(index.json), 보고서(runs/)
```
새 직원은 `staff/새직원.py`, `.github/workflows/새직원.yml`, `staff.json` 항목을 추가하면 됩니다.

## 알아둘 점
- YouTube 무료 할당량은 하루 10,000이고, 헌터 1회에 약 870을 씁니다(하루 약 8,700).
- 다른 사람 영상의 **시청 지속시간**은 YouTube가 공개하지 않습니다. 그래서 좋아요율, 구매의도 댓글, 아웃라이어로 추정합니다.
- 영상 화면은 직접 볼 수 없습니다. AI는 제목, 설명, 태그, 댓글로 판단합니다.
- 테무는 공개 API가 없어 검색 링크만 제공합니다. 쿠팡은 나중에 파트너스 API를 연결할 수 있습니다.
- 아마존은 서버 접속을 막는 경우가 많습니다. 막히면 건너뛰고, 헌터의 영상 데이터로 제품을 고릅니다.
