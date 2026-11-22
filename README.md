# cert-promo

자격증 앱(cert-apps)의 홈 배너와 대체 광고에 쓰는 JSON. GitHub Pages가 `docs/`를 내보낸다.

- 주소: `https://mangorocketofficial.github.io/cert-promo/<종목>.json`
- 내용: `coupang`(쿠팡 파트너스 검색 API 상품, 앱별 subId 링크), `house`(자체 배너)
- 갱신: `.github/workflows/build.yml`이 6시간마다 `tools/build_promo.py`를 돌린다. 저장소 Secrets에 `COUPANG_ACCESS_KEY`, `COUPANG_SECRET_KEY`가 있어야 한다.
- 바꾸기: 검색어·채널·자체 배너는 `promo_config.json`. 고쳐서 push하면 바로 다시 만든다.
- 쿠팡 API가 실패한 종목은 이전 상품을 그대로 둔다.

키는 이 저장소에 넣지 않는다(공개 저장소).
