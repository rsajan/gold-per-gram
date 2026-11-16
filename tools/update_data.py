"""Rebuilds data.js: gold price per gram in USD (open/high/low/close) plus USD/INR and USD/SGD rates.

The page multiplies the two to get gold in rupees and Singapore dollars.

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
    """Returns {date: (open, high, low, close)}."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?period1=0&period2=4000000000&interval=1d"
    res = json.loads(get(url))["chart"]["result"][0]
    gmt = res["meta"].get("gmtoffset", 0)
    q = res["indicators"]["quote"][0]
    out = {}
    for ts, o, h, l, c in zip(res["timestamp"], q["open"], q["high"], q["low"], q["close"]):
        if c is None or c <= 0:
            continue
        # Bars are stamped at local midnight using today's UTC offset, so summer dates land at 23:00
        # the day before; a two-hour nudge puts every bar on its own trading day.
        day = (datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=ts + gmt + 7200)).date()
        out[day] = (o, h, l, float(c))
    return out


def drop_glitches(series, limit):
    """Remove single-day bad ticks whose close jumps more than `limit` from the last good close."""
    clean, prev = {}, None
    for day in sorted(series):
        c = series[day][3]
        if prev is not None and abs(c / prev - 1) > limit:
            continue
        clean[day] = series[day]
        prev = c
    return clean


def tidy_bar(o, h, l, c):
    """Open/high/low are sometimes missing or wild; fall back to the close so candles stay sane."""
    ok = lambda v: v is not None and v > 0 and abs(v / c - 1) < 0.15
    o = float(o) if ok(o) else c
    h = float(h) if ok(h) else c
    l = float(l) if ok(l) else c
    return o, max(h, o, c), min(l, o, c), c


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
    inr_d = {d: v[3] for d, v in drop_glitches(yahoo("INR=X"), 0.06).items()}
    sgd_d = {d: v[3] for d, v in drop_glitches(yahoo("SGD=X"), 0.06).items()}
    mgold = monthly_gold()
    inr_m, sgd_m = monthly_fx()

    first_daily = min(gold)
    months = []
    y, m = 1970, 1
    while (y, m) < (first_daily.year, first_daily.month + 1):
        months.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    monthly = {"start": months[0], "usd": [], "fxInr": [], "fxSgd": []}
    for key in months:
        monthly["usd"].append(round(mgold[key] / OZ, 4))
        monthly["fxInr"].append(round(inr_m[key], 4) if key in inr_m else None)
        monthly["fxSgd"].append(round(sgd_m[key], 4) if key in sgd_m else None)

    daily = {"t": [], "o": [], "h": [], "l": [], "c": [], "fxInr": [], "fxSgd": []}
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
        o, h, l, c = tidy_bar(*gold[day])
        daily["t"].append((day - EPOCH).days)
        for k, v in (("o", o), ("h", h), ("l", l), ("c", c)):
            daily[k].append(round(v / OZ, 3))
        daily["fxInr"].append(round(r_inr, 4))
        daily["fxSgd"].append(round(r_sgd, 5))
    if len(daily["t"]) < 5000:
        raise RuntimeError("daily history looks incomplete")
    return {"v": 2, "monthly": monthly, "daily": daily}


def load_existing():
    text = OUT.read_text(encoding="utf-8")
    data = json.loads(re.search(r"=\s*(\{.*\})\s*;?\s*$", text, re.S).group(1))
    if data.get("v") != 2:
        raise RuntimeError("data.js is in an older format")
    return data


def append_latest(data):
    """Fallback when Yahoo is unreachable: add the newest day from the currency API."""
    j = json.loads(get("https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/xau.json"))
    date = datetime.date.fromisoformat(j["date"])
    day = (date - EPOCH).days
    d = data["daily"]
    if day <= d["t"][-1] or date.weekday() >= 5:
        return False
    x = j["xau"]
    c = round(x["usd"] / OZ, 3)
    d["t"].append(day)
    for k in "ohlc":
        d[k].append(c)
    d["fxInr"].append(round(x["inr"] / x["usd"], 4))
    d["fxSgd"].append(round(x["sgd"] / x["usd"], 5))
    return True


def main():
    try:
        data = full_build()
        mode = "full rebuild"
    except Exception as e:  # noqa: BLE001
        print("Full rebuild failed:", e, file=sys.stderr)
        data = load_existing()
        mode = "appended latest day" if append_latest(data) else "no new data"
        data.pop("updated", None)
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
    print(f"{mode}: {len(d['t'])} daily points to {last}, last USD/g {d['c'][-1]}, USD/INR {d['fxInr'][-1]}, USD/SGD {d['fxSgd'][-1]}")


if __name__ == "__main__":
    main()
