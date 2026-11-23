#!/usr/bin/env python3
"""종목별 배너 JSON(docs/<종목>.json)을 만든다.

쿠팡 파트너스 검색 API로 종목 검색어의 상품을 받아 앱별 채널(subId)이 붙은 링크로 저장하고,
promo_config.json의 자체 배너(house)를 함께 넣는다. 앱은 이 JSON만 받아 쓴다.

    COUPANG_ACCESS_KEY=... COUPANG_SECRET_KEY=... python3 tools/build_promo.py
    python3 tools/build_promo.py --env /path/.env      # 로컬 시험 (KEY=값 또는 KEY:값)
    python3 tools/build_promo.py --dry-run             # 호출 없이 이번에 고를 종목만 출력

검색 API 한도는 계정 전체 시간당 약 10회다. 그래서 한 번 돌릴 때
  - 최근 1시간 호출 기록(state/coupang_calls.json)을 빼고 남은 만큼, 최대 MAX_PER_RUN회만 부른다.
  - 새 종목(파일 없음)부터, 그다음 coupang_updated가 오래된 종목부터 고른다.
  - 검색어와 subId가 모두 같은 종목끼리만 호출 1회를 나눠 쓴다. 응답 링크(link.coupang.com/re/...)에
    subid와 함께 요청별 traceid·requestid·token이 박혀 있어 subId를 바꿔 끼워 공유할 수 없다.
  - 403/429(한도 초과)가 나면 그 실행의 나머지 호출을 멈추고, COOLDOWN 동안 호출하지 않는다.
API가 실패한 종목은 이전 파일의 쿠팡 상품을 그대로 둔다(빈 배너가 되지 않게).
"""
import argparse
import datetime as dt
import hashlib
import hmac
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
HOST = "https://api-gateway.coupang.com"
SEARCH = "/v2/providers/affiliate_open_api/apis/openapi/v1/products/search"
STATE = ROOT / "state" / "coupang_calls.json"

MAX_PER_RUN = 9        # 한 번에 부를 최대 횟수
HOURLY_BUDGET = 9      # 최근 1시간 동안 쓸 최대 횟수(한도 약 10회에서 1회 여유)
COOLDOWN = dt.timedelta(hours=1)   # 403/429 뒤 쉬는 시간


class RateLimited(Exception):
    pass


def load_env(path):
    keys = {}
    for line in pathlib.Path(path).expanduser().read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        sep = "=" if "=" in line else ":"
        k, _, v = line.partition(sep)
        keys[k.strip().upper().replace("-", "_")] = v.strip().strip('"')
    return keys


def auth_header(method, path, query, access, secret):
    signed = dt.datetime.now(dt.timezone.utc).strftime("%y%m%dT%H%M%SZ")
    message = signed + method + path + query
    sig = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return (f"CEA algorithm=HmacSHA256, access-key={access}, "
            f"signed-date={signed}, signature={sig}")


def search(keyword, sub_id, limit, access, secret):
    query = urllib.parse.urlencode({"keyword": keyword, "limit": limit, "subId": sub_id})
    req = urllib.request.Request(
        f"{HOST}{SEARCH}?{query}",
        headers={
            "Authorization": auth_header("GET", SEARCH, query, access, secret),
            "Content-Type": "application/json;charset=UTF-8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            raise RateLimited(f"HTTP {e.code}") from None
        raise RuntimeError(f"HTTP {e.code}") from None
    if str(body.get("rCode")) != "0":
        raise RuntimeError(f"rCode={body.get('rCode')} {body.get('rMessage')}")
    items = []
    for p in (body.get("data") or {}).get("productData") or []:
        url = p.get("productUrl") or ""
        if not url.startswith("https://"):
            continue
        items.append({
            "id": f"cp-{p.get('productId')}",
            "kind": "coupang",
            "title": p.get("productName", "").strip(),
            "url": url,
            "image": p.get("productImage") or "",
            "price": p.get("productPrice"),
            "rocket": bool(p.get("isRocket")),
        })
    return items


def parse_time(s):
    try:
        return dt.datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def load_state(now):
    try:
        state = json.loads(STATE.read_text())
    except (OSError, ValueError):
        state = {}
    calls = [t for t in (parse_time(s) for s in state.get("calls", [])) if t and now - t < dt.timedelta(hours=2)]
    return {"calls": calls, "rate_limited": parse_time(state.get("rate_limited"))}


def save_state(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "_comment": "쿠팡 검색 API 호출 시각(최근 2시간). build_promo.py가 시간당 한도를 지키려고 쓴다.",
        "calls": [t.isoformat(timespec="seconds") for t in sorted(state["calls"])],
        "rate_limited": state["rate_limited"].isoformat(timespec="seconds") if state["rate_limited"] else None,
    }
    STATE.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")


def plan(config, out, budget):
    """이번에 부를 (검색어, subId) 묶음을 고른다. 반환: [(key, [종목...])], 종목별 이전 문서."""
    previous, order = {}, []
    for cert in config["certs"]:
        path = out / f"{cert}.json"
        previous[cert] = json.loads(path.read_text()) if path.exists() else None
        updated = parse_time((previous[cert] or {}).get("coupang_updated"))
        # 새 종목(파일 없음) → 갱신 기록 없음 → 오래된 순
        order.append((previous[cert] is not None, updated is not None,
                      updated or dt.datetime.min.replace(tzinfo=dt.timezone.utc), cert))
    order.sort()

    groups = {}
    for cert, spec in config["certs"].items():
        groups.setdefault((spec["keywords"][0], spec["sub_id"]), []).append(cert)

    picked, seen = [], set()
    for *_, cert in order:
        spec = config["certs"][cert]
        key = (spec["keywords"][0], spec["sub_id"])
        if key in seen:
            continue
        if len(picked) >= budget:
            break
        seen.add(key)
        picked.append((key, groups[key]))
    return picked, previous


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", help="로컬 시험용 키 파일")
    ap.add_argument("--out", default=str(ROOT / "docs"))
    ap.add_argument("--dry-run", action="store_true", help="호출·저장 없이 이번에 고를 종목만 출력")
    ap.add_argument("--max", type=int, default=MAX_PER_RUN, help="이번 실행의 최대 호출 수")
    args = ap.parse_args()

    config = json.loads((ROOT / "promo_config.json").read_text())
    out = pathlib.Path(args.out)
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    limit = config.get("coupang_limit", 6)

    state = load_state(now)
    recent = sum(1 for t in state["calls"] if now - t < dt.timedelta(hours=1))
    budget = max(0, min(args.max, MAX_PER_RUN, HOURLY_BUDGET - recent))
    if state["rate_limited"] and now - state["rate_limited"] < COOLDOWN:
        budget = 0
        print(f"쿠팡 한도 초과 뒤 쉬는 중({state['rate_limited'].isoformat()}부터 {COOLDOWN})")
    picked, previous = plan(config, out, budget)

    print(f"종목 {len(config['certs'])}개, 최근 1시간 호출 {recent}회, 이번 호출 가능 {budget}회, 고른 묶음 {len(picked)}개")
    for (keyword, sub_id), certs in picked:
        when = [(previous[c] or {}).get("coupang_updated") or ("새 종목" if previous[c] is None else "기록 없음") for c in certs]
        print(f"  {', '.join(certs)}  ← '{keyword}' subId={sub_id}  (이전 갱신: {', '.join(when)})")
    if args.dry_run:
        return

    results = {}
    attempted = failed = 0
    if picked:
        env = dict(os.environ)
        if args.env:
            env.update(load_env(args.env))
        access, secret = env.get("COUPANG_ACCESS_KEY"), env.get("COUPANG_SECRET_KEY")
        if not access or not secret:
            sys.exit("COUPANG_ACCESS_KEY / COUPANG_SECRET_KEY가 없다")
        for i, ((keyword, sub_id), certs) in enumerate(picked):
            if i:
                time.sleep(1)
            attempted += 1
            state["calls"].append(dt.datetime.now(dt.timezone.utc).replace(microsecond=0))
            try:
                items = search(keyword, sub_id, limit, access, secret)
                for c in certs:
                    results[c] = items
                print(f"{', '.join(certs)}: 쿠팡 {len(items)}개")
            except RateLimited as e:
                failed += 1
                state["rate_limited"] = now
                print(f"{', '.join(certs)}: 쿠팡 한도 초과({e}). 이번 실행의 나머지 호출을 멈춘다", file=sys.stderr)
                break
            except Exception as e:  # 한 종목이 실패해도 나머지는 만든다
                failed += 1
                print(f"{', '.join(certs)}: 쿠팡 실패, 이전 값 유지 ({e})", file=sys.stderr)
        save_state(state)

    # 새 결과가 있거나 자체 배너가 바뀐 종목 파일만 다시 쓴다(종목이 많아도 커밋이 작게).
    out.mkdir(parents=True, exist_ok=True)
    written = 0
    stamp = now.isoformat(timespec="seconds")
    for cert, spec in config["certs"].items():
        prev = previous[cert] or {}
        house = config.get("house", []) + spec.get("house", [])
        doc = {
            "version": 1,
            "generated": prev.get("generated", stamp),
            "coupang_updated": prev.get("coupang_updated"),
            "coupang": prev.get("coupang", []),
            "house": [dict(h, kind="house") for h in house if h.get("url", "").startswith("https://")],
        }
        if cert in results:
            doc["coupang"], doc["coupang_updated"] = results[cert], stamp
        if doc == prev:
            continue
        doc["generated"] = stamp
        (out / f"{cert}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
        written += 1
    index = out / "index.html"
    if not index.exists():
        index.write_text("<!doctype html><title>cert promo</title>\n")
    print(f"파일 {written}개 갱신")

    if attempted and failed == attempted:
        sys.exit("이번에 부른 쿠팡 API가 모두 실패했다")


if __name__ == "__main__":
    main()
