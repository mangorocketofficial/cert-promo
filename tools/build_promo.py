#!/usr/bin/env python3
"""종목별 배너 JSON(docs/<종목>.json)을 만든다.

쿠팡 파트너스 검색 API로 종목 검색어의 상품을 받아 앱별 채널(subId)이 붙은 링크로 저장하고,
promo_config.json의 자체 배너(house)를 함께 넣는다. 앱은 이 JSON만 받아 쓴다.

    COUPANG_ACCESS_KEY=... COUPANG_SECRET_KEY=... python3 tools/build_promo.py
    python3 tools/build_promo.py --env /path/.env      # 로컬 시험 (KEY=값 또는 KEY:값)

검색 API는 계정 전체에서 시간당 약 10회라 한 번 돌릴 때 종목마다 검색어 1개만 부른다.
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
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
HOST = "https://api-gateway.coupang.com"
SEARCH = "/v2/providers/affiliate_open_api/apis/openapi/v1/products/search"


def load_env(path):
    keys = {}
    for line in pathlib.Path(path).read_text().splitlines():
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
    with urllib.request.urlopen(req, timeout=20) as r:
        body = json.load(r)
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", help="로컬 시험용 키 파일")
    ap.add_argument("--out", default=str(ROOT / "docs"))
    args = ap.parse_args()

    env = dict(os.environ)
    if args.env:
        env.update(load_env(args.env))
    access, secret = env.get("COUPANG_ACCESS_KEY"), env.get("COUPANG_SECRET_KEY")
    if not access or not secret:
        sys.exit("COUPANG_ACCESS_KEY / COUPANG_SECRET_KEY가 없다")

    config = json.loads((ROOT / "promo_config.json").read_text())
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    limit = config.get("coupang_limit", 6)
    failed = 0

    for cert, spec in config["certs"].items():
        path = out / f"{cert}.json"
        previous = json.loads(path.read_text()) if path.exists() else {}
        coupang = previous.get("coupang", [])
        updated = previous.get("coupang_updated")
        try:
            coupang = search(spec["keywords"][0], spec["sub_id"], limit, access, secret)
            updated = now
            print(f"{cert}: 쿠팡 {len(coupang)}개")
        except Exception as e:  # 한 종목이 실패해도 나머지는 만든다
            failed += 1
            print(f"{cert}: 쿠팡 실패, 이전 값 유지 ({e})", file=sys.stderr)
        house = config.get("house", []) + spec.get("house", [])
        doc = {
            "version": 1,
            "generated": now,
            "coupang_updated": updated,
            "coupang": coupang,
            "house": [dict(h, kind="house") for h in house if h.get("url", "").startswith("https://")],
        }
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
        time.sleep(1)

    (out / "index.html").write_text("<!doctype html><title>cert promo</title>\n")
    if failed == len(config["certs"]):
        sys.exit("모든 종목에서 쿠팡 API가 실패했다")


if __name__ == "__main__":
    main()
