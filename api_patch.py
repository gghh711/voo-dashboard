
# ── 加在 app.py 的 webhook 路由附近 ──────────────────────────────
# 把這段加在 @server.route("/webhook") 的前面或後面

import json
import math

@server.route("/api/stock")
def api_stock():
    """
    GET /api/stock?code=2330&days=365
    回傳最近 N 日的差值、點數、差值差
    """
    code = flask.request.args.get("code", "").strip().upper()
    days = int(flask.request.args.get("days", 365))

    if not code:
        return flask.jsonify({"error": "請提供股票代碼"}), 400

    # 台股加 .TW
    ticker = code + ".TW" if code.isdigit() else code

    try:
        import datetime
        from datetime import timezone
        end_dt   = datetime.datetime.now(tz=timezone.utc)
        start_dt = end_dt - datetime.timedelta(days=days)

        dates, closes, volumes, opens = fetch_yahoo_range(ticker, start_dt, end_dt, "1d")

        # 需要高低價來算 ADX，再抓一次含 high/low
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
               f"?interval=1d&period1={int(start_dt.timestamp())}&period2={int(end_dt.timestamp())}")
        r = requests.get(url, headers=HEADERS, timeout=15)
        data = r.json()
        result = data["chart"]["result"][0]
        quote  = result["indicators"]["quote"][0]
        highs  = quote.get("high",  [None]*len(closes))
        lows   = quote.get("low",   [None]*len(closes))
        raw_closes = quote.get("close", closes)
        ts_list    = result["timestamp"]

        # 過濾 null
        valid = []
        for i in range(len(ts_list)):
            if raw_closes[i] is None or highs[i] is None or lows[i] is None:
                continue
            valid.append({
                "ts": ts_list[i],
                "h":  highs[i],
                "l":  lows[i],
                "c":  raw_closes[i],
                "o":  quote["open"][i] if quote.get("open") and quote["open"][i] else raw_closes[i],
            })

        # ADX 14日計算
        def calc_adx(rows, p=14):
            n = len(rows)
            TR, PDM, NDM = [], [], []
            for i in range(1, n):
                h, l, c, pc = rows[i]["h"], rows[i]["l"], rows[i]["c"], rows[i-1]["c"]
                TR.append(max(h-l, abs(h-pc), abs(l-pc)))
                up = h - rows[i-1]["h"]
                dn = rows[i-1]["l"] - l
                PDM.append(up if up > dn and up > 0 else 0)
                NDM.append(dn if dn > up and dn > 0 else 0)

            def ws(a):
                s = sum(a[:p])
                r = [s]
                for i in range(p, len(a)):
                    s = s - s/p + a[i]
                    r.append(s)
                return r

            sTR  = ws(TR)
            sPDM = ws(PDM)
            sNDM = ws(NDM)

            DX = []
            for i in range(len(sTR)):
                tr = sTR[i]
                pi = 100*sPDM[i]/tr if tr else 0
                ni = 100*sNDM[i]/tr if tr else 0
                DX.append(100*abs(pi-ni)/(pi+ni) if (pi+ni) else 0)

            adx = sum(DX[:p]) / p
            ADX = [adx]
            for i in range(p, len(DX)):
                adx = (adx*(p-1) + DX[i]) / p
                ADX.append(adx)

            pad = [None] * (p*2 - 1)
            return pad + ADX

        adx_arr = calc_adx(valid)

        def to_pt(adx):
            if adx is None or math.isnan(adx):
                return None
            d = min(94.5, max(0, adx))
            return min(21, max(0, round(d / 94.5 * 21)))

        # 組合回傳資料（最近60筆，從新到舊）
        rows_out = []
        for i in range(len(valid)-1, max(-1, len(valid)-61), -1):
            adx      = adx_arr[i]
            diff     = round(min(94.5, max(0, adx)), 1) if adx is not None else None
            prev_adx = adx_arr[i-1] if i > 0 else None
            prev_diff = round(min(94.5, max(0, prev_adx)), 1) if prev_adx is not None else None
            dd       = round(diff - prev_diff, 2) if diff is not None and prev_diff is not None else None
            pt       = to_pt(adx)
            pr       = valid[i]
            chg      = round(pr["c"] - valid[i-1]["c"], 2) if i > 0 else None
            chg_p    = round(chg / valid[i-1]["c"] * 100, 2) if chg is not None and valid[i-1]["c"] else None

            import datetime as dt
            d_obj = dt.datetime.fromtimestamp(pr["ts"], tz=timezone.utc)
            ds = d_obj.strftime("%Y%m%d")

            rows_out.append({
                "date":  ds,
                "close": round(pr["c"], 2),
                "chg":   chg,
                "chg_p": chg_p,
                "diff":  diff,
                "dd":    dd,
                "pt":    pt,
            })

        meta   = result.get("meta", {})
        name   = meta.get("longName") or meta.get("shortName") or code
        return flask.jsonify({
            "code": code,
            "name": name,
            "rows": rows_out,
        })

    except Exception as e:
        return flask.jsonify({"error": str(e)}), 500
