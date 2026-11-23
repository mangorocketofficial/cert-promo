# cert-promo

자격증 앱(cert-apps)의 홈 배너와 대체 광고에 쓰는 JSON. GitHub Pages가 `docs/`를 내보낸다.

- 주소: `https://mangorocketofficial.github.io/cert-promo/<종목>.json`
- 내용: `coupang`(쿠팡 파트너스 검색 API 상품, 앱별 subId 링크), `coupang_updated`(쿠팡 상품을 마지막으로 받은 시각), `house`(자체 배너)
- 갱신: `.github/workflows/build.yml`이 1시간마다(매시 17분) `tools/build_promo.py`를 돌린다. 저장소 Secrets에 `COUPANG_ACCESS_KEY`, `COUPANG_SECRET_KEY`가 있어야 한다.
- 쿠팡 API가 실패한 종목은 이전 상품을 그대로 둔다.

키는 이 저장소에 넣지 않는다(공개 저장소).

## 쿠팡 검색 API 한도

검색 API는 계정 전체에서 시간당 약 10회다(다른 곳에서 같은 키로 부르는 것도 포함). 그래서 한 번 돌 때

- 최근 1시간 호출 기록(`state/coupang_calls.json`)을 빼고 **시간당 9회**, 한 번에 **최대 9회**만 부른다. 수동 실행이나 push로 한 시간에 여러 번 돌아도 합계 9회를 넘지 않는다.
- 새 종목(`docs/<종목>.json` 없음)부터, 그다음 `coupang_updated`가 가장 오래된 종목부터 고른다.
- 검색어와 subId가 **둘 다 같은** 종목끼리만 호출 1회를 나눠 쓴다. 응답 링크(`link.coupang.com/re/...`)에 subid와 함께 요청별 traceid·requestid·token이 들어 있어서 subId만 바꿔 끼울 수 없다. 그래서 subId가 다르면 검색어가 같아도 따로 부른다.
- 403/429(한도 초과)가 나면 그 실행의 나머지 호출을 멈추고 1시간 동안 부르지 않는다(반복하면 계정 API가 막힐 수 있다).

종목 수가 N개면 한 종목이 다시 갱신되는 주기는 약 `N ÷ 9`시간이다.

| 종목 수 | 갱신 주기 |
|---|---|
| 9 | 1시간 |
| 50 | 약 6시간 |
| 100 | 약 11시간 |
| 200 | 약 22시간 (하루 1번 꼴) |

## 새 종목 추가

1. `promo_config.json`의 `certs`에 `"<종목>": { "sub_id": "<종목>app", "keywords": ["<검색어>"] }`를 넣는다. 종목 키는 앱이 부르는 파일 이름(`<종목>.json`)과 같아야 하고, `sub_id`는 종목마다 달라야 쿠팡 실적이 앱별로 나뉜다. 검색어는 첫 번째 것만 쓴다.
2. push하면 워크플로가 바로 돈다. 새 종목은 파일이 없어서 가장 먼저 고른다. 이번 시간 한도가 남아 있지 않으면 다음 정시 실행 때 만든다.
3. 확인: `python3 tools/build_promo.py --dry-run`은 호출 없이 이번에 고를 종목을 보여 준다.

종목별 자체 배너는 `certs.<종목>.house`에, 모든 종목 공통 배너는 맨 위 `house`에 넣는다.

## 로컬에서 돌리기

```bash
python3 tools/build_promo.py --dry-run                     # 이번에 고를 종목만 출력(키 필요 없음)
python3 tools/build_promo.py --env /path/.env --max 1      # 실제로 1회만 호출(KEY=값 또는 KEY:값)
```

로컬에서 부른 것도 같은 계정 한도를 쓴다. 로컬 실행 뒤 `state/coupang_calls.json`을 커밋해야 워크플로가 그 호출을 센다.
