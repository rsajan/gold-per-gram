"""Rebuilds data.js with gold prices per gram in USD, INR and SGD.

Sources (all free, no API key):
  - Daily gold (USD/oz): Yahoo Finance COMEX gold futures GC=F, 2000-08-30 onward
  - Daily FX: Yahoo Finance INR=X and SGD=X, 2003-12 onward
  - Monthly gold 1970-2000 and monthly FX: github.com/datasets (World Bank / FRED)
  - Fallback for the newest day: fawazahmed0 currency-api
Run:  python tools/update_data.py
"""
import csv, io, json, re, sys, time, datetime, urllib.request
from pathlib import Path

OZ = 31.1034768
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data.js"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
EPOCH = datetime.date(1970, 1, 1)


def get(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode("utf-8")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (i + 1))
    raise last


def yahoo(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?period1=0&period2=4000000000&interval=1d"
    res = json.loads(get(url))["chart"]["result"][0]
    gmt = res["meta"].get("gmtoffset", 0)
    out = {}
    for ts, c in zip(res["timestamp"], res["indicators"]["quote"][0]["close"]):
        if c is None or c <= 0:
            continue
        # Bars are stamped at local midnight using today's UTC offset, so summer dates land at 23:00
        # the day before; a two-hour nudge puts every bar on its own trading day.
        day = (datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=ts + gmt + 7200)).date()
        out[day] = float(c)
    return out


def drop_glitches(series, limit):
    """Remove single-day bad ticks that jump more than `limit` from the last good value."""
    clean, prev = {}, None
    for day in sorted(series):
        v = series[day]
        if prev is not None and abs(v / prev - 1) > limit:
            continue
        clean[day] = v
        prev = v
    return clean


def monthly_gold():
    rows = csv.DictReader(io.StringIO(get("https://raw.githubusercontent.com/datasets/gold-prices/main/data/monthly.csv")))
    return {r["Date"]: float(r["Price"]) for r in rows}


def monthly_fx():
    rows = csv.DictReader(io.StringIO(get("https://raw.githubusercontent.com/datasets/exchange-rates/main/data/monthly.csv")))
    fx = {"India": {}, "Singapore": {}}
    for r in rows:
        if r["Country"] in fx and r["Exchange rate"]:
            try:
                fx[r["Country"]][r["Date"][:7]] = float(r["Exchange rate"])
            except ValueError:
                pass
    return fx["India"], fx["Singapore"]


def full_build():
    gold = drop_glitches(yahoo("GC=F"), 0.20)
    inr_d = drop_glitches(yahoo("INR=X"), 0.06)
    sgd_d = drop_glitches(yahoo("SGD=X"), 0.06)
    mgold = monthly_gold()
    inr_m, sgd_m = monthly_fx()

    first_daily = min(gold)
    months = []
    y, m = 1970, 1
    while (y, m) < (first_daily.year, first_daily.month + 1):
        months.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    monthly = {"start": months[0], "usd": [], "inr": [], "sgd": []}
    for key in months:
        g = mgold[key] / OZ
        monthly["usd"].append(round(g, 4))
        monthly["inr"].append(round(g * inr_m[key], 2) if key in inr_m else None)
        monthly["sgd"].append(round(g * sgd_m[key], 4) if key in sgd_m else None)

    daily = {"t": [], "usd": [], "inr": [], "sgd": []}
    fx_days_inr, fx_days_sgd = sorted(inr_d), sorted(sgd_d)
    ii = si = 0
    last_inr = last_sgd = None
    for day in sorted(gold):
        while ii < len(fx_days_inr) and fx_days_inr[ii] <= day:
            last_inr = inr_d[fx_days_inr[ii]]
            ii += 1
        while si < len(fx_days_sgd) and fx_days_sgd[si] <= day:
            last_sgd = sgd_d[fx_days_sgd[si]]
            si += 1
        key = day.strftime("%Y-%m")
        r_inr = last_inr if last_inr else inr_m.get(key)
        r_sgd = last_sgd if last_sgd else sgd_m.get(key)
        if not r_inr or not r_sgd:
            continue
        g = gold[day] / OZ
        daily["t"].append((day - EPOCH).days)
        daily["usd"].append(round(g, 3))
        daily["inr"].append(round(g * r_inr, 1))
        daily["sgd"].append(round(g * r_sgd, 3))
    if len(daily["t"]) < 5000:
        raise RuntimeError("daily history looks incomplete")
    return {"monthly": monthly, "daily": daily}


def load_existing():
    text = OUT.read_text(encoding="utf-8")
    return json.loads(re.search(r"=\s*(\{.*\})\s*;?\s*$", text, re.S).group(1))


def append_latest(data):
    """Fallback when Yahoo is unreachable: add the newest day from the currency API."""
    j = json.loads(get("https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/xau.json"))
    day = (datetime.date.fromisoformat(j["date"]) - EPOCH).days
    d = data["daily"]
    if day <= d["t"][-1]:
        return False
    d["t"].append(day)
    d["usd"].append(round(j["xau"]["usd"] / OZ, 3))
    d["inr"].append(round(j["xau"]["inr"] / OZ, 1))
    d["sgd"].append(round(j["xau"]["sgd"] / OZ, 3))
    return True


def main():
    try:
        data = full_build()
        mode = "full rebuild"
    except Exception as e:  # noqa: BLE001
        print("Full rebuild failed:", e, file=sys.stderr)
        if not OUT.exists():
            raise
        data = load_existing()
        mode = "appended latest day" if append_latest(data) else "no new data"
    if OUT.exists():
        try:
            old = load_existing()
            old.pop("updated", None)
            if old == data:
                print("Prices unchanged; data.js left as it is.")
                return
        except Exception:  # noqa: BLE001
            pass
    data["updated"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    OUT.write_text("window.GOLD_DATA = " + json.dumps(data, separators=(",", ":")) + ";\n", encoding="utf-8")
    d = data["daily"]
    last = EPOCH + datetime.timedelta(days=d["t"][-1])
    print(f"{mode}: {len(d['t'])} daily points to {last}, last USD/g {d['usd'][-1]}, INR/g {d['inr'][-1]}, SGD/g {d['sgd'][-1]}")


if __name__ == "__main__":
    main()
