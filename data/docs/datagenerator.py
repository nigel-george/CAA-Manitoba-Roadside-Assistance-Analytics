
import os
import json
import time
import calendar
import numpy as np
import pandas as pd

# =====================================================================
# 0. SETTINGS
# =====================================================================
SEED = 42
OUT_RAW = "data/raw"
OUT_DOCS = "data/docs"
INJECT_DIRTY = True

DATA_END = np.datetime64("2026-10-17", "D")

# ---- published anchors (2024 annual report, Manitoba) -------------------
A_RESCUES_2024 = 111_973
A_TIRES_2024 = 11_253
A_BATT_2024 = 3_828

# ---- geography ------------------------------------------------------------
# Geo_ID, City, Zone, Lat, Lon, request share (K), P(response meets target)
CITIES = [
    (1, "Winnipeg", "Urban", 49.8951, -97.1384, 34.0, 0.94),
    (2, "Selkirk", "Suburban", 50.1436, -96.8836, 1.8, 0.93),
    (3, "Steinbach", "Suburban", 49.5258, -96.6835, 2.6, 0.92),
    (4, "Portage la Prairie", "Rural", 49.9728, -98.2913, 2.0, 0.90),
    (5, "Winkler-Morden", "Rural", 49.1828, -97.9454, 2.4, 0.91),
    (6, "Brandon", "Rural", 49.8486, -99.9501, 6.0, 0.91),
    (7, "Dauphin", "Rural", 51.1495, -100.0502, 1.4, 0.84),
    (8, "Thompson", "Remote", 55.7435, -97.8558, 2.6, 0.76),
    (9, "Flin Flon", "Remote", 54.7658, -101.8763, 1.2, 0.79),
    (10, "The Pas", "Remote", 53.8251, -101.2532, 1.0, 0.81),
]
ZONE_TARGET_MIN = {"Urban": 45, "Suburban": 50, "Rural": 60, "Remote": 90}
CITY_P = np.array([c[5] for c in CITIES]) / sum(c[5] for c in CITIES)
GEO_ZONE = {c[0]: c[2] for c in CITIES}
GEO_NAME = {c[0]: c[1] for c in CITIES}
GEO_TARGET_MIN = np.array([0] + [ZONE_TARGET_MIN[c[2]] for c in CITIES])
GEO_ATT = np.array([0] + [c[6] for c in CITIES])

# ---- service types ---------------------------------------------------------
SERVICES = ["Towing", "Battery", "Flat Tire", "Lockout", "Fuel"]
SVC_BASE_P = np.array([0.44, 0.30, 0.10, 0.11, 0.05])
SVC_ATT = np.array([0.896, 0.935, 0.925, 0.920, 0.950])
ATT_CENTER = 0.916
SVC_SEASON = {
    **{m: np.array([1.05, 1.45, 0.80, 1.00, 0.90]) for m in (12, 1, 2)},
    **{m: np.array([1.00, 1.15, 0.90, 1.00, 1.00]) for m in (3, 11)},
    **{m: np.array([0.95, 0.75, 1.20, 1.00, 1.10]) for m in (6, 7, 8)},
}
YEAR_ATT_ADJ = {2024: -0.022, 2025: -0.012, 2026: 0.0}
YEAR_SAT_ADJ = {2024: -0.15, 2025: -0.10, 2026: 0.0}

# ---- contact channel (ASSUMPTIONS - not in the annual report) -----------------
CHANNELS = {1: "Phone", 2: "App", 3: "Web", 4: "Unknown"}
APP_BASE = np.array([0, 0.48, 0.42, 0.35, 0.22])        # index = zone code below (YTD-2026 level)
WEB_BASE = np.array([0, 0.08, 0.08, 0.07, 0.06])
ZONE_CODE = {"Urban": 1, "Suburban": 2, "Rural": 3, "Remote": 4}
GEO_ZONE_CODE = np.array([0] + [ZONE_CODE[c[2]] for c in CITIES])
APP_G0, APP_G_SLOPE = 0.66, 0.01193      # app adoption multiplier: Jan-2024 = 0.66 ... Oct-2026 ~ 1.05
P_UNKNOWN_CHANNEL = 0.01
CH_RESP_FACTOR = {1: 1.08, 2: 0.90, 3: 0.98, 4: 1.04}   # app requests are answered faster
CH_ATT_ADJ = {1: -0.020, 2: 0.025, 3: 0.0, 4: 0.0}
CH_SAT_ADJ = {1: -0.05, 2: 0.12, 3: 0.05, 4: 0.0}

# ---- monthly request volumes (K) ----------------------------------------------
M25 = [11.7, 12.4, 9.9, 8.1, 8.5, 9.2, 10.4, 9.9, 8.3, 8.4, 9.2, 10.7]    # = 116.7K
M26 = [12.4, 13.3, 10.4, 8.5, 9.0, 9.7, 11.3, 10.6, 8.8, 5.0]             # Oct = 1-17 only
HEAT = np.array([           # Mon..Sun x [12A, 4A, 8A, 12P, 4P, 8P]
    [1.4, 1.6, 3.8, 2.5, 4.1, 1.4], [1.3, 1.6, 4.0, 2.5, 4.1, 1.4],
    [1.4, 1.6, 3.8, 2.7, 4.1, 1.4], [1.4, 1.6, 4.0, 2.7, 4.1, 1.4],
    [1.6, 1.6, 3.8, 2.9, 4.9, 1.4], [2.0, 1.3, 2.3, 2.7, 2.9, 1.4],
    [1.6, 1.1, 1.8, 2.3, 2.0, 1.1]])
HEAT_P = HEAT / HEAT.sum(axis=1, keepdims=True)
DOW_W = HEAT.sum(axis=1)

A_MET, B_MET = 1.9, 2.4
OVER_BASE, OVER_K, OVER_SCALE = 0.05, 1.5, 0.20
SURVEY_RESPONSE_RATE = 0.25


# =====================================================================
# 1. HELPERS
# =====================================================================
def banner(msg):
    print("\n" + "=" * 70 + f"\n{msg}\n" + "=" * 70)


def month_starts(first=(2024, 1), last=(2026, 10)):
    out, y, m = [], first[0], first[1]
    while (y, m) <= last:
        out.append((y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def days_in(y, m):
    return 17 if (y, m) == (2026, 10) else calendar.monthrange(y, m)[1]


def monthly_counts():
    m25 = [int(round(v * 1000)) for v in M25]
    m26 = [int(round(v * 1000)) for v in M26]
    raw = np.array(M25) * 1000 * A_RESCUES_2024 / sum(m25)
    m24 = np.floor(raw).astype(int)
    m24[np.argsort(-(raw - m24))[:A_RESCUES_2024 - m24.sum()]] += 1     # exact 111,973
    counts = {}
    for i, n in enumerate(m24):
        counts[(2024, i + 1)] = int(n)
    for i, n in enumerate(m25):
        counts[(2025, i + 1)] = n
    for i, n in enumerate(m26):
        counts[(2026, i + 1)] = n
    return counts


# =====================================================================
# 2. BUILD THE CLEAN REQUESTS (the "truth")
# =====================================================================
def build_requests():
    banner("STEP 1/3  Building roadside requests")
    t0 = time.time()
    rng = np.random.default_rng([SEED, 20])
    idx_of = {k: i for i, k in enumerate(month_starts())}
    frames = []
    for (y, m), n in monthly_counts().items():
        last = days_in(y, m)
        days = np.arange(last)
        first = np.datetime64(f"{y}-{m:02d}-01", "D")
        dow = ((first + days.astype("timedelta64[D]")).astype(np.int64) + 3) % 7      # 0 = Monday
        day_idx = rng.choice(last, n, p=DOW_W[dow] / DOW_W[dow].sum())
        dow_arr = dow[day_idx]
        block = np.zeros(n, dtype=int)
        for w in range(7):
            sel = dow_arr == w
            block[sel] = rng.choice(6, sel.sum(), p=HEAT_P[w])
        hour = block * 4 + rng.integers(0, 4, n)
        ts = (first + day_idx.astype("timedelta64[D]")).astype("datetime64[s]") \
            + hour.astype("timedelta64[h]") + rng.integers(0, 60, n).astype("timedelta64[m]") \
            + rng.integers(0, 60, n).astype("timedelta64[s]")
        sp = SVC_BASE_P * SVC_SEASON.get(m, np.ones(5))
        geo = rng.choice(np.arange(1, 11), n, p=CITY_P)

        # contact channel: app adoption grows over time and differs by zone
        zc = GEO_ZONE_CODE[geo]
        g = APP_G0 + APP_G_SLOPE * idx_of[(y, m)]
        app_p = np.clip(APP_BASE[zc] * g, 0, 0.80)
        web_p = WEB_BASE[zc]
        u = rng.random(n)
        ch = np.where(u < app_p, 2, np.where(u < app_p + web_p, 3, 1))
        ch = np.where(rng.random(n) < P_UNKNOWN_CHANNEL, 4, ch)

        frames.append(pd.DataFrame({
            "ts": ts, "Year": y, "Month": m, "Geo_ID": geo, "ch": ch,
            "svc": rng.choice(5, n, p=sp / sp.sum())}))
    req = pd.concat(frames, ignore_index=True).sort_values("ts", kind="stable").reset_index(drop=True)
    n = len(req)

    # --- exact 2024 anchors: tire changes and battery replacements
    svc = req["svc"].values.copy()
    yr = req["Year"].values
    y24 = np.flatnonzero(yr == 2024)
    flat24 = y24[svc[y24] == 2]
    if len(flat24) > A_TIRES_2024:
        svc[rng.choice(flat24, len(flat24) - A_TIRES_2024, replace=False)] = 0
    elif len(flat24) < A_TIRES_2024:
        tow24 = y24[svc[y24] == 0]
        svc[rng.choice(tow24, A_TIRES_2024 - len(flat24), replace=False)] = 2
    req["svc"] = svc
    batt_rows = np.flatnonzero(svc == 1)
    batt24 = batt_rows[yr[batt_rows] == 2024]
    rep = np.zeros(n, dtype=int)
    rep[rng.choice(batt24, A_BATT_2024, replace=False)] = 1
    other = batt_rows[yr[batt_rows] != 2024]
    rep[other[rng.random(len(other)) < A_BATT_2024 / len(batt24)]] = 1
    req["Battery_Replaced"] = rep

    # --- response model: city / service / year / channel decide attainment
    geo = req["Geo_ID"].values
    ch = req["ch"].values
    T = GEO_TARGET_MIN[geo]
    f_ch = np.vectorize(CH_RESP_FACTOR.get)(ch)
    p_ch = np.vectorize(CH_ATT_ADJ.get)(ch)
    s_ch = np.vectorize(CH_SAT_ADJ.get)(ch)
    p_met = np.clip(GEO_ATT[geo] + req["Year"].map(YEAR_ATT_ADJ).values
                    + (SVC_ATT[svc] - ATT_CENTER) + p_ch, 0.30, 0.995)
    met = rng.random(n) < p_met
    lo = np.maximum(6, np.round(0.18 * T))
    met_resp = lo + (T - lo) * rng.beta(A_MET, B_MET, n) * f_ch
    unmet_resp = T + 1 + T * (OVER_BASE + rng.gamma(OVER_K, OVER_SCALE, n)) * f_ch
    resp = np.rint(np.where(met, met_resp, unmet_resp)).astype(int)
    resp = np.where(met, np.minimum(resp, T), np.maximum(resp, T + 1))
    req["Target_Minutes"] = T
    req["Resp"] = resp
    req["Met"] = (resp <= T).astype(int)
    req["Arrival"] = req["ts"].values + resp.astype("timedelta64[m]")

    pending = (req["ts"].values >= np.datetime64("2026-10-16T00:00:00")) & (resp > T) & (rng.random(n) < 0.35)
    req["Status"] = np.where(pending, "Pending", "Completed")
    sat = 5.0 - 0.015 * resp + req["Year"].map(YEAR_SAT_ADJ).values + s_ch + rng.normal(0, 0.45, n)
    sat = np.clip(np.rint(sat), 1, 5)
    req["Satisfaction_Score"] = np.where((rng.random(n) < SURVEY_RESPONSE_RATE) & ~pending, sat, np.nan)

    req["Service_Type"] = np.array(SERVICES)[svc]
    req["Contact_Channel"] = pd.Series(ch).map(CHANNELS).values
    req["City"] = req["Geo_ID"].map(GEO_NAME)
    req["Zone"] = req["Geo_ID"].map(GEO_ZONE)
    req.insert(0, "Request_ID", [f"REQ-{i}" for i in range(100_001, 100_001 + n)])
    print(f"  requests: {n:,}   ({time.time() - t0:.0f}s)")
    return req


# =====================================================================
# 3. TIE-OUT CHECKS (on the CLEAN data)
# =====================================================================
def run_checks(req):
    banner("STEP 2/3  Tie-out checks (clean data)")
    rows = []

    def add(name, actual, target, tol):
        rows.append(dict(Check=name, Target=target, Actual=round(float(actual), 4), Tolerance=tol,
                         Status="PASS" if abs(actual - target) <= tol else "FAIL"))

    ts = req["ts"].values
    w26 = (ts >= np.datetime64("2026-01-01")) & (ts < np.datetime64("2026-10-18"))
    w25 = (ts >= np.datetime64("2025-01-01")) & (ts < np.datetime64("2025-10-18"))
    y24, y25 = req["Year"].values == 2024, req["Year"].values == 2025
    a, b = req[w26], req[w25]

    add("2024 rescues (exact)", y24.sum(), A_RESCUES_2024, 0)
    add("2024 tire changes (exact)", ((req["Service_Type"] == "Flat Tire") & y24).sum(), A_TIRES_2024, 0)
    add("2024 battery replacements (exact)", req.loc[y24, "Battery_Replaced"].sum(), A_BATT_2024, 0)
    add("2024 tire share of rescues %", ((req["Service_Type"] == "Flat Tire") & y24).sum() / y24.sum() * 100, 10.05, 0.1)
    add("2025 rescues", y25.sum(), 116_700, 600)
    add("2026 YTD rescues", w26.sum(), 99_000, 600)
    add("2025 same-window rescues", w25.sum(), 93_000, 700)
    add("Request growth YoY % (YTD window)", (w26.sum() / w25.sum() - 1) * 100, 6.4, 0.7)
    add("Winnipeg share of requests %", (a["City"] == "Winnipeg").mean() * 100, 61.8, 1.5)

    add("Target attainment 2026 YTD %", a["Met"].mean() * 100, 91.6, 0.5)
    add("Attainment change vs same window (pts)", (a["Met"].mean() - b["Met"].mean()) * 100, 0.9, 0.7)
    add("Attainment: Winnipeg %", a[a["City"] == "Winnipeg"]["Met"].mean() * 100, 94.0, 0.7)
    add("Attainment: Remote zone %", a[a["Zone"] == "Remote"]["Met"].mean() * 100, 77.8, 1.5)
    add("Attainment: Thompson %", a[a["City"] == "Thompson"]["Met"].mean() * 100, 75.5, 2.5)
    sa = a.groupby("Service_Type")["Met"].mean() * 100
    add("Attainment: Towing %", sa["Towing"], 89.6, 0.8)
    add("Towing is the only service below 90%", int((sa < 90).sum() == 1 and sa["Towing"] < 90), 1, 0)
    add("Avg response (min)", a["Resp"].mean(), 32.0, 2.0)
    add("P90 response (min)", np.percentile(a["Resp"], 90), 55.0, 5.0)
    add("Avg satisfaction (1-5)", a["Satisfaction_Score"].mean(), 4.5, 0.12)
    add("Avg satisfaction: Remote", a[a["Zone"] == "Remote"]["Satisfaction_Score"].mean(), 4.1, 0.2)
    add("Satisfaction vs response correlation (<0)", np.corrcoef(
        a.dropna(subset=["Satisfaction_Score"])["Resp"], a.dropna(subset=["Satisfaction_Score"])["Satisfaction_Score"])[0, 1] < 0, 1, 0)

    mix = a["Service_Type"].value_counts() / 1000
    for s, t in zip(["Towing", "Battery", "Lockout", "Flat Tire", "Fuel"], [43.5, 29.7, 10.9, 9.9, 5.0]):
        add(f"Requests 2026 YTD: {s} (K)", mix[s], t, 2.0)

    # channel (planted assumptions)
    jan24 = req[(req["Year"] == 2024) & (req["Month"] == 1)]
    add("App share Jan 2024 %", (jan24["Contact_Channel"] == "App").mean() * 100, 28.0, 2.5)
    add("App share 2026 YTD %", (a["Contact_Channel"] == "App").mean() * 100, 42.0, 2.5)
    add("Unknown channel % (2026 YTD)", (a["Contact_Channel"] == "Unknown").mean() * 100, 1.0, 0.4)
    rem = a[a["Zone"] == "Remote"]
    add("Remote phone share %", (rem["Contact_Channel"] == "Phone").mean() * 100, 72.0, 4.0)
    urb = a[a["Zone"] == "Urban"]
    add("Urban app share %", (urb["Contact_Channel"] == "App").mean() * 100, 48.0, 3.0)
    ph, ap = a[a["Contact_Channel"] == "Phone"], a[a["Contact_Channel"] == "App"]
    add("Response gap phone - app (min)", ph["Resp"].mean() - ap["Resp"].mean(), 5.5, 2.5)
    add("Satisfaction gap app - phone", ap["Satisfaction_Score"].mean() - ph["Satisfaction_Score"].mean(), 0.2, 0.12)

    rep = pd.DataFrame(rows)
    pd.set_option("display.width", 170, "display.max_rows", 200, "display.max_colwidth", 60)
    print(rep.to_string(index=False))
    fails = int((rep["Status"] == "FAIL").sum())
    print(f"\n  {len(rep) - fails}/{len(rep)} checks passed" + ("" if fails == 0 else f"   ({fails} FAIL - see above)"))

    print("\n  Numbers to narrate from (2026 YTD, clean data):")
    byc = a.groupby("Contact_Channel").agg(share=("Resp", "size"), resp=("Resp", "mean"),
                                           sat=("Satisfaction_Score", "mean"), att=("Met", "mean"))
    byc["share"] = byc["share"] / len(a) * 100
    print(byc.round(2).to_string())
    print(a.groupby("Zone")["Met"].mean().mul(100).round(1).to_string())
    return rep


# =====================================================================
# 4. WRITE THE 39 RAW FILES (with dirty data)
# =====================================================================
CITY_VARIANTS = {
    "Winnipeg": ["winnipeg", "WINNIPEG", "Winnipeg, MB", "Wpg", "Winnipeg "],
    "Selkirk": ["selkirk", "Selkirk, MB"],
    "Steinbach": ["steinbach", "Steinbach, MB", "Steinbach "],
    "Portage la Prairie": ["Portage La Prairie", "Portage", "portage la prairie "],
    "Winkler-Morden": ["Winkler", "Winkler/Morden", "winkler-morden"],
    "Brandon": ["brandon", "BRANDON", "Brandon, MB", "Brandon "],
    "Dauphin": ["dauphin", "Dauphin "],
    "Thompson": ["thompson", "Thompson, MB", "Thompson "],
    "Flin Flon": ["Flin-Flon", "flin flon", "FlinFlon"],
    "The Pas": ["the pas", "The Pas "],
}
CHANNEL_VARIANTS = {
    "Phone": ["phone", "PHONE", "Phone ", "Call", "Call Centre", "Telephone"],
    "App": ["app", "APP", "Mobile App", "CAA App", "Mobile app "],
    "Web": ["web", "WEB", "Online", "Website"],
}
SVC_VARIANTS = {"Towing": [" Towing", "towing "], "Battery": ["BATTERY", "Battery "],
                "Flat Tire": ["Flat tire", "flat tire "], "Lockout": ["lockout ", "LOCKOUT"],
                "Fuel": ["FUEL", "fuel "]}


def vary(series, variants, frac, rng):
    s = series.copy().astype(object)
    for i in np.flatnonzero(rng.random(len(s)) < frac):
        opts = variants.get(s.iat[i])
        if opts:
            s.iat[i] = opts[rng.integers(len(opts))]
    return s


def write_files(req, rep):
    banner("STEP 3/3  Writing files")
    os.makedirs(OUT_RAW, exist_ok=True)
    os.makedirs(OUT_DOCS, exist_ok=True)
    rng = np.random.default_rng([SEED, 50])
    next_id = 100_001 + len(req) + 1000
    n_req = 0
    raw_rows = 0
    for (y, m) in month_starts():
        sub = req[(req["Year"] == y) & (req["Month"] == m)]
        df = pd.DataFrame({
            "Request_ID": sub["Request_ID"].values, "Request_DateTime": sub["ts"].values,
            "City": sub["City"].values, "Service_Type": sub["Service_Type"].values,
            "Contact_Channel": sub["Contact_Channel"].values,
            "Target_Minutes": sub["Target_Minutes"].values, "Arrival_DateTime": sub["Arrival"].values,
            "Status": sub["Status"].values, "Satisfaction_Score": sub["Satisfaction_Score"].values,
            "Battery_Replaced": sub["Battery_Replaced"].values})
        us_format = False
        if INJECT_DIRTY:
            n = len(df)
            seed = int(y * 100 + m)
            canc = df.sample(int(n * 0.015), random_state=seed).copy()
            canc["Request_ID"] = [f"REQ-{next_id + i}" for i in range(len(canc))]
            next_id += len(canc)
            canc["Status"] = "Cancelled"
            canc["Arrival_DateTime"] = pd.NaT
            canc["Satisfaction_Score"] = np.nan
            canc["Battery_Replaced"] = 0
            neg = df.sample(max(int(n * 0.002), 3), random_state=seed + 7).copy()
            neg["Request_ID"] = [f"REQ-{next_id + i}" for i in range(len(neg))]
            next_id += len(neg)
            neg["Arrival_DateTime"] = neg["Request_DateTime"] - pd.to_timedelta(rng.integers(5, 40, len(neg)), unit="m")
            neg["Satisfaction_Score"] = np.nan
            dup = df.sample(int(n * 0.005), random_state=seed + 13).copy()
            for part in (df, canc, neg, dup):
                part["City"] = vary(part["City"], CITY_VARIANTS, 0.08, rng)
                part["Contact_Channel"] = vary(part["Contact_Channel"], CHANNEL_VARIANTS, 0.35, rng)
            for part in (df, canc, neg, dup):
                part["Contact_Channel"] = part["Contact_Channel"].replace("Unknown", "")   # blank in the raw file
            df["Service_Type"] = vary(df["Service_Type"], SVC_VARIANTS, 0.03, rng)
            df = pd.concat([df, canc, neg, dup], ignore_index=True)
            us_format = ((y * 12 + m) % 4 == 0)            # every 4th file stores dates as US text
        else:
            df["Contact_Channel"] = df["Contact_Channel"].replace("Unknown", "")
        fmt = "%m/%d/%Y %I:%M %p" if us_format else "%Y-%m-%d %H:%M:%S"
        for c in ("Request_DateTime", "Arrival_DateTime"):
            df[c] = pd.to_datetime(df[c]).dt.strftime(fmt)
        df.to_csv(os.path.join(OUT_RAW, f"requests_{y}_{m:02d}.csv"), index=False)
        n_req += 1
        raw_rows += len(df)
    print(f"  requests_YYYY_MM.csv x {n_req}   ({raw_rows:,} raw rows -> {len(req):,} clean)")

    # ---- dimensions
    pd.DataFrame([(c[0], c[1], c[2], c[3], c[4], ZONE_TARGET_MIN[c[2]]) for c in CITIES],
                 columns=["Geo_ID", "City", "Zone", "Latitude", "Longitude", "Zone_Target_Min"]
                 ).to_csv(os.path.join(OUT_RAW, "dim_location.csv"), index=False)
    pd.DataFrame({"Service_Type_ID": range(1, 6), "Service_Name": SERVICES}
                 ).to_csv(os.path.join(OUT_RAW, "dim_service_type.csv"), index=False)
    pd.DataFrame({"Channel_ID": list(CHANNELS), "Channel_Name": list(CHANNELS.values())}
                 ).to_csv(os.path.join(OUT_RAW, "dim_channel.csv"), index=False)

    # ---- one reusable mapping table for City and Channel
    rows = []
    for field, variants in (("City", CITY_VARIANTS), ("Channel", CHANNEL_VARIANTS)):
        for std, vs in variants.items():
            for v in {std, *vs}:
                rows.append((field, v, v.strip().lower(), std))
    mp = pd.DataFrame(rows, columns=["Field", "Raw_Value", "Match_Key", "Standard_Value"]) \
        .drop_duplicates(["Field", "Match_Key"]).sort_values(["Field", "Standard_Value", "Match_Key"])
    mp.to_csv(os.path.join(OUT_RAW, "name_mapping.csv"), index=False)

    # ---- targets + annual report anchors (JSON)
    with open(os.path.join(OUT_RAW, "targets.json"), "w") as f:
        json.dump({
            "attainment_target_pct": 0.90,
            "data_end_date": "2026-10-17",
            "zone_target_minutes": ZONE_TARGET_MIN,
            "annual_report_2024": {
                "source": "CAA 2024 annual report (figures as provided)", "scope": "Manitoba",
                "rescues": A_RESCUES_2024, "tire_changes": A_TIRES_2024,
                "battery_replacements": A_BATT_2024},
            "note": "Concept demo - 2025 and 2026 are simulated; contact channel is an assumption."}, f, indent=2)
    print("  dim_location.csv, dim_service_type.csv, dim_channel.csv, name_mapping.csv, targets.json")

    # ---- helper files (not loaded into Power BI)
    rep.to_csv(os.path.join(OUT_DOCS, "checks_report.csv"), index=False)
    dd = [
        ("requests_YYYY_MM.csv", "Request_ID", "text", "Unique per request. About 0.5% duplicated -> remove duplicates"),
        ("requests_YYYY_MM.csv", "Request_DateTime, Arrival_DateTime", "text",
         "ISO 'yyyy-MM-dd HH:mm:ss' in most files; every 4th file is US text 'MM/dd/yyyy hh:mm AM/PM'"),
        ("requests_YYYY_MM.csv", "City", "text", "Messy spellings -> Trim + lowercase, merge with name_mapping (Field = City)"),
        ("requests_YYYY_MM.csv", "Service_Type", "text", "Case / whitespace -> Trim + Proper"),
        ("requests_YYYY_MM.csv", "Contact_Channel", "text",
         "Phone / App / Web in many spellings; ~1% blank -> merge with name_mapping (Field = Channel), blank = Unknown"),
        ("requests_YYYY_MM.csv", "Target_Minutes", "int", "Zone response target: 45 / 50 / 60 / 90"),
        ("requests_YYYY_MM.csv", "Status", "text", "Completed / Pending / Cancelled. Cancelled rows have no arrival time -> filter out"),
        ("requests_YYYY_MM.csv", "Satisfaction_Score", "1-5 or blank", "About 25% of requests are rated"),
        ("requests_YYYY_MM.csv", "Battery_Replaced", "0/1", "Ties to the published battery replacements"),
        ("requests_YYYY_MM.csv", "(derive in Power Query)", "",
         "Response_Minutes, Met_Target, Overshoot_Min, Request_Date, Day_Name, Day_Sort, Hour_Block, Hour_Block_Sort; "
         "keep only Response_Minutes >= 0; keys Geo_ID, Service_Type_ID, Channel_ID via merges"),
        ("dim_location.csv", "Geo_ID, City, Zone, Latitude, Longitude, Zone_Target_Min", "", "10 Manitoba cities. Hierarchy Zone -> City"),
        ("dim_service_type.csv", "Service_Type_ID, Service_Name", "", "5 service types"),
        ("dim_channel.csv", "Channel_ID, Channel_Name", "", "Phone, App, Web, Unknown"),
        ("name_mapping.csv", "Field, Raw_Value, Match_Key, Standard_Value", "", "Filter Field = City or Channel before merging; match on lowercase trimmed text"),
        ("targets.json", "attainment_target_pct, zone_target_minutes, annual_report_2024", "", "Parse with Json.Document; used for target lines and the Model vs Published table"),
    ]
    pd.DataFrame(dd, columns=["File", "Column(s)", "Type", "Notes"]).to_csv(
        os.path.join(OUT_DOCS, "data_dictionary.csv"), index=False)
    print("  docs: checks_report.csv, data_dictionary.csv")
    n_files = len([f for f in os.listdir(OUT_RAW)])
    print(f"\n  Files in {OUT_RAW}/: {n_files}  (expected 39)")


def main():
    t0 = time.time()
    print("CAA Manitoba roadside data generator (simulated data)")
    req = build_requests()
    rep = run_checks(req)
    write_files(req, rep)
    banner(f"DONE in {time.time() - t0:.0f}s  ->  load the files in '{OUT_RAW}/' into Power BI")


if __name__ == "__main__":
    main()