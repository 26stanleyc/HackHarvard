# ruff: noqa: E501  (HTML templates)
"""Synthetic bills, charts and reports for the bid-extraction feature (Phase 3) and its evaluation.

    cd backend && uv run python scripts/make_extract_fixtures.py     # writes HTML + ground truth
    # then render PNGs (see fixtures/extract/README.md)

Every document is fictional (company, retailer and utility names are made up) and carries a
"SAMPLE — synthetic test document" footer. Formats follow public descriptions of real
documents: Texas commercial bills (retail energy charge per kWh; delivery charges per kWh and
per kW of 15-minute peak demand; a separate 4CP transmission line), GHG Protocol Scope 2
inventories (MWh x grid factor, location- and market-based), depot charging schedules and
hourly load charts. Each encodes one bid with a known answer in ``ground_truth.json``; some
need arithmetic (monthly kWh / billing days -> MWh per day), as a real document would.
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "fixtures" / "extract"
SRC = OUT / "src"

CSS = """
body{margin:0;background:#fff;font:14px/1.45 Helvetica,Arial,sans-serif;color:#1d2433}
.doc{width:820px;padding:28px 34px;box-sizing:border-box}
h1{font-size:20px;margin:0 0 2px} h2{font-size:15px;margin:18px 0 6px}
.muted{color:#5b6475;font-size:12px} .row{display:flex;justify-content:space-between;gap:24px}
table{border-collapse:collapse;width:100%;margin-top:6px} td,th{border-bottom:1px solid #d8dde6;
padding:5px 6px;text-align:left;font-size:13px} th{background:#f2f4f8} td.n,th.n{text-align:right}
.box{border:1px solid #c9d1de;border-radius:6px;padding:10px 12px;margin-top:12px;background:#f8fafc}
.brand{font-weight:700;letter-spacing:.3px} .total{font-weight:700;font-size:16px}
.foot{margin-top:18px;border-top:1px dashed #b8c0cc;padding-top:6px;color:#8a93a3;font-size:11px}
"""
FOOT = ('<div class="foot">SAMPLE — synthetic test document for Carbon Clearinghouse. '
        "All names and figures are fictional.</div>")


def page(body: str) -> str:
    return (f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head>"
            f"<body><div class='doc' id='doc'>{body}{FOOT}</div></body></html>")


def bars(values, *, stack=None, labels=("base", "flexible"), colors=("#9aa7bd", "#2a78d6"),
         unit="MW", width=750, height=260, title="") -> str:
    """Hourly bar chart (24 bars) as inline SVG; optional stacked second series."""
    stack = stack or [0] * 24
    top = max(a + b for a, b in zip(values, stack, strict=True)) * 1.15 or 1
    pl, pb, pt = 46, 28, 18
    cw, ch = width - pl - 10, height - pb - pt
    bw = cw / 24
    out = [f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg" '
           f'font-family="Helvetica,Arial" font-size="11">']
    if title:
        out.append(f'<text x="{pl}" y="12" font-weight="bold">{title}</text>')
    for i in range(5):
        v = top * i / 4
        y = pt + ch - ch * i / 4
        out.append(f'<line x1="{pl}" x2="{width - 10}" y1="{y:.1f}" y2="{y:.1f}" stroke="#e3e7ee"/>'
                   f'<text x="{pl - 6}" y="{y + 4:.1f}" text-anchor="end">{v:.0f}</text>')
    out.append(f'<text x="8" y="{pt + ch / 2}" transform="rotate(-90 8 {pt + ch / 2})" '
               f'text-anchor="middle">{unit}</text>')
    for h in range(24):
        x = pl + h * bw + 2
        y0 = pt + ch
        for v, c in ((values[h], colors[0]), (stack[h], colors[1])):
            if v:
                hgt = ch * v / top
                y0 -= hgt
                out.append(f'<rect x="{x:.1f}" y="{y0:.1f}" width="{bw - 4:.1f}" '
                           f'height="{hgt:.1f}" fill="{c}"/>')
        if h % 3 == 0:
            out.append(f'<text x="{x + bw / 2 - 2:.1f}" y="{height - 10}" text-anchor="middle">'
                       f'{h:02d}:00</text>')
    lx = width - 230
    for k, (lab, c) in enumerate(zip(labels, colors, strict=True)):
        if k == 1 and not any(stack):
            continue
        out.append(f'<rect x="{lx + k * 110}" y="4" width="10" height="10" fill="{c}"/>'
                   f'<text x="{lx + 14 + k * 110}" y="13">{lab}</text>')
    out.append("</svg>")
    return "".join(out)


def tx_bill(*, retailer, utility, customer, address, esid, period, days, kwh, peak_kw,
            peak_time, notes, energy_rate=0.0612) -> str:
    energy = kwh * energy_rate
    cust = 45.00
    dist_kwh = kwh * 0.00071
    demand = peak_kw * 9.84
    tcp = peak_kw * 0.62 * 4.21
    total = energy + cust + dist_kwh + demand + tcp
    return page(f"""
<div class="row"><div><div class="brand" style="font-size:18px">{retailer}</div>
<div class="muted">Retail electric provider · Commercial service</div></div>
<div style="text-align:right"><div class="muted">Statement date</div><b>{period[1]}</b></div></div>
<div class="row" style="margin-top:14px"><div><b>{customer}</b><br>{address}</div>
<div><div class="muted">ESI ID</div>{esid}<div class="muted" style="margin-top:4px">Service period</div>
{period[0]} – {period[1]} ({days} days)</div></div>
<h2>Usage summary</h2>
<table><tr><th>Meter</th><th class="n">Energy used</th><th class="n">Peak demand (15-min)</th>
<th>Peak recorded</th></tr>
<tr><td>Main service</td><td class="n">{kwh:,.0f} kWh</td><td class="n">{peak_kw:,.0f} kW</td>
<td>{peak_time}</td></tr></table>
<h2>Charges</h2>
<table><tr><th>Item</th><th class="n">Quantity</th><th class="n">Rate</th><th class="n">Amount</th></tr>
<tr><td>Energy charge ({retailer})</td><td class="n">{kwh:,.0f} kWh</td><td class="n">$0.0612/kWh</td>
<td class="n">${energy:,.2f}</td></tr>
<tr><td colspan=4 class="muted">Delivery charges from {utility} (passed through at tariff)</td></tr>
<tr><td>Customer charge</td><td class="n">1</td><td class="n">$45.00</td><td class="n">${cust:,.2f}</td></tr>
<tr><td>Distribution system charge</td><td class="n">{kwh:,.0f} kWh</td><td class="n">$0.00071/kWh</td>
<td class="n">${dist_kwh:,.2f}</td></tr>
<tr><td>Distribution demand charge</td><td class="n">{peak_kw:,.0f} kW</td><td class="n">$9.84/kW</td>
<td class="n">${demand:,.2f}</td></tr>
<tr><td>Transmission (4CP)</td><td class="n">{peak_kw * 0.62:,.0f} kW</td><td class="n">$4.21/kW</td>
<td class="n">${tcp:,.2f}</td></tr>
<tr><td class="total" colspan=3>Total current charges</td><td class="n total">${total:,.2f}</td></tr></table>
<div class="box">{notes}</div>""")


DOCS: list[dict] = []


def add(name, kind, html, truth):
    DOCS.append({"file": f"{name}.png", "kind": kind, "html": html, "truth": truth})


add("bill_01", "utility bill (EV depot)", tx_bill(
    retailer="Bluebonnet Retail Energy", utility="Central Texas Wires Co.",
    customer="Prairie Line Logistics — East Depot", address="4100 Industrial Blvd, Waco TX",
    esid="10443720000981774", period=("Mar 1, 2024", "Mar 30, 2024"), days=30, kwh=234_000,
    peak_kw=1_600, peak_time="Mar 12, 00:45",
    notes="<b>Interval data summary.</b> 94% of this period's energy was used between 00:00 "
          "and 06:00, when the electric van fleet charges at the depot. Charging must be "
          "complete by 06:00 for the first dispatch. Peak demand is set by the depot chargers."),
    {"earliest_hour": 0, "deadline_hour": 6, "energy_mwh": 7.332, "max_mw": 1.6,
     "how": "94% of 234,000 kWh is charging: 219,960 kWh / 30 days = 7.332 MWh per day; "
            "1,600 kW peak = 1.6 MW. (The first answer key said 7.8, ignoring the 94%; the "
            "model's first extraction caught it, 2026-10-18.)"})

base_dc = [35] * 24
batch = [20 if 13 <= h < 19 else 0 for h in range(24)]
add("load_chart_01", "hourly load chart (data center)", page(f"""
<h1>Halcyon Compute — Austin campus</h1><div class="muted">Hourly load, typical weekday (MW)</div>
<div style="margin-top:10px">{bars(base_dc, stack=batch, labels=("base IT load", "batch queue"),
                               title="Campus load by hour (MW)")}</div>
<div class="box"><b>Flexible batch queue.</b> The batch queue uses 120 MWh per day at up to 20 MW
(it currently runs 13:00–19:00). Jobs can run in any hour of the day but must finish by
midnight (24:00) for the daily SLA. Base IT load is not flexible.</div>"""),
    {"earliest_hour": 0, "deadline_hour": 24, "energy_mwh": 120, "max_mw": 20,
     "how": "stated: 120 MWh at up to 20 MW, any hour, done by 24:00"})

add("charging_schedule_01", "fleet charging schedule", page("""
<h1>Gulf Coast Parcel — Fleet charging plan</h1><div class="muted">Depot 7 · weekday schedule</div>
<table><tr><th>Group</th><th class="n">Vans</th><th class="n">Energy to replenish</th>
<th>Back at depot</th></tr>
<tr><td>Route A (city)</td><td class="n">40</td><td class="n">3,200 kWh</td><td>17:30</td></tr>
<tr><td>Route B (suburban)</td><td class="n">36</td><td class="n">3,600 kWh</td><td>17:45</td></tr>
<tr><td>Route C (regional)</td><td class="n">20</td><td class="n">2,800 kWh</td><td>18:00</td></tr>
<tr><td><b>Total</b></td><td class="n"><b>96</b></td><td class="n"><b>9,600 kWh</b></td><td></td></tr>
</table>
<div class="box"><b>Chargers:</b> 32 × 75 kW Level-2/DC units (2.4 MW combined; the site
contract caps the depot at that). <b>Charging window:</b> from 18:00, once all vans are back,
until 24:00 — the landlord's power agreement ends depot charging at midnight.</div>"""),
    {"earliest_hour": 18, "deadline_hour": 24, "energy_mwh": 9.6, "max_mw": 2.4,
     "how": "9,600 kWh = 9.6 MWh; 32 x 75 kW = 2.4 MW; 18:00 to 24:00"})

add("bill_02", "chiller sub-meter statement (offices)", page("""
<div class="brand" style="font-size:18px">Mesquite Facility Services</div>
<div class="muted">Sub-metered energy statement · Chiller plants</div>
<h1 style="margin-top:12px">Riverbend Office Portfolio (12 buildings)</h1>
<div class="muted">Billing period: Jun 3 – Jun 28, 2024 · 20 workdays</div>
<table><tr><th>Meter</th><th class="n">Energy</th><th class="n">Max demand</th></tr>
<tr><td>Chiller plants (all buildings)</td><td class="n">360,000 kWh</td><td class="n">6,000 kW</td></tr>
<tr><td>Lighting and plug loads (not flexible)</td><td class="n">410,500 kWh</td>
<td class="n">2,950 kW</td></tr></table>
<div class="box"><b>Pre-cooling program.</b> Chillers pre-cool the buildings before occupancy:
they may run from 04:00 and the buildings must be at set-point by 09:00, when occupancy
starts. Combined chiller capacity is 6 MW. All chiller energy above is pre-cooling, used on
workdays only.</div>"""),
    {"earliest_hour": 4, "deadline_hour": 9, "energy_mwh": 18, "max_mw": 6,
     "how": "360,000 kWh / 20 workdays = 18 MWh per day; 6,000 kW = 6 MW; 04:00 to 09:00"})

add("scope2_report_01", "Scope 2 emissions report (cold storage)", page("""
<h1>Northstar Cold Chain — 2024 Scope 2 inventory (excerpt)</h1>
<div class="muted">Prepared under the GHG Protocol Scope 2 Guidance · Texas sites</div>
<table><tr><th>Facility</th><th class="n">Electricity (MWh/yr)</th>
<th class="n">Location-based (tCO2e)</th><th class="n">Market-based (tCO2e)</th></tr>
<tr><td>Houston DC-1 (frozen)</td><td class="n">71,540</td><td class="n">26,470</td><td class="n">19,850</td></tr>
<tr><td>Dallas DC-2 (chilled)</td><td class="n">58,400</td><td class="n">21,610</td><td class="n">21,610</td></tr>
<tr><td><b>Total</b></td><td class="n"><b>129,940</b></td><td class="n"><b>48,080</b></td>
<td class="n"><b>41,460</b></td></tr></table>
<div class="muted">Location-based factor: ERCOT grid average, 0.37 tCO2e/MWh (illustrative).
Market-based: supplier-specific factors and RECs.</div>
<h2>Flexibility register</h2>
<div class="box">Pre-chilling of the frozen rooms can be shifted without affecting product
temperature: <b>160 MWh per day</b>, compressors up to <b>20 MW</b>, any time between
<b>00:00 and 16:00</b> (rooms must be pre-chilled before the 16:00 inbound trucks).</div>"""),
    {"earliest_hour": 0, "deadline_hour": 16, "energy_mwh": 160, "max_mw": 20,
     "how": "stated in the flexibility register"})

add("bill_03", "utility bill (water pumping)", tx_bill(
    retailer="Pecos Valley Power", utility="Central Texas Wires Co.",
    customer="City of Lampasas Springs — Raw Water Pump Station 2",
    address="Rt 9 Pump Station Rd, Lampasas Springs TX", esid="10443720000310552",
    period=("Aug 1, 2024", "Aug 30, 2024"), days=30, kwh=2_880_000, peak_kw=12_000,
    peak_time="Aug 19, 03:15",
    notes="<b>Operating note.</b> The pumps (12 MW installed) refill the treated-water "
          "reservoir every day. Pumping may happen at any time between 00:00 and 16:00, as "
          "long as the reservoir is full before the evening demand peak."),
    {"earliest_hour": 0, "deadline_hour": 16, "energy_mwh": 96, "max_mw": 12,
     "how": "2,880,000 kWh / 30 days = 96 MWh per day; 12,000 kW = 12 MW"})

bus = [0] * 24
for h, v in {9: 1.2, 10: 1.2, 11: 1.1, 12: 0.9, 13: 0.8, 14: 0.8}.items():
    bus[h] = v
add("load_chart_02", "charging load chart (school buses)", page(f"""
<h1>Pine Hollow ISD — Bus barn charging</h1>
<div class="muted">Midday top-up between routes, current pattern (MW)</div>
<div style="margin-top:10px">{bars([round(x * 10) / 10 for x in bus], labels=("charging", ""),
                               title="Bus barn charging by hour (MW)", unit="MW")}</div>
<div class="box">40 electric buses each need about 150 kWh between the morning and afternoon
routes (6.0 MWh in total). The barn has 20 chargers rated 60 kW. Buses are back at 09:00
and must leave at 15:00.</div>"""),
    {"earliest_hour": 9, "deadline_hour": 15, "energy_mwh": 6.0, "max_mw": 1.2,
     "how": "40 x 150 kWh = 6.0 MWh; 20 x 60 kW = 1.2 MW; 09:00 to 15:00"})

add("job_queue_01", "job scheduler screen (training run)", page("""
<div style="background:#111827;color:#e5e7eb;padding:14px 16px;border-radius:8px">
<div style="font-weight:700;font-size:16px">Lattice Scheduler · Job #48213</div>
<div style="color:#9ca3af;font-size:12px">cluster: tx-west-2 · queue: deferrable</div>
<table style="color:#e5e7eb;margin-top:10px">
<tr><td>Job</td><td>vision-model fine-tune (checkpointable)</td></tr>
<tr><td>Power draw</td><td>8.0 MW (112 racks)</td></tr>
<tr><td>Runtime at full power</td><td>15 h</td></tr>
<tr><td>Earliest start</td><td>06:00 (inputs ready)</td></tr>
<tr><td>Deadline (SLA)</td><td>24:00 same day</td></tr>
<tr><td>Can pause / resume</td><td>yes, hourly checkpoints</td></tr>
</table></div>
<div class="box">Deferrable jobs may be placed in any hours between their earliest start and
deadline, at up to their listed power draw.</div>"""),
    {"earliest_hour": 6, "deadline_hour": 24, "energy_mwh": 120, "max_mw": 8,
     "how": "8.0 MW x 15 h = 120 MWh; 06:00 to 24:00"})

add("bill_04", "cold storage invoice (MWh units)", page("""
<div class="row"><div><div class="brand" style="font-size:18px">Trinity Edge Energy</div>
<div class="muted">Wholesale-indexed invoice · amounts in MWh</div></div>
<div style="text-align:right" class="muted">Invoice 2024-0917-552</div></div>
<h1 style="margin-top:12px">Coastal Freeze Partners — Corpus Christi plant</h1>
<div class="muted">Service: Sep 1 – Sep 30, 2024 (30 days)</div>
<table><tr><th>Load</th><th class="n">MWh</th><th class="n">Peak MW</th><th>Hours</th></tr>
<tr><td>Night pre-freeze (blast freezers)</td><td class="n">720</td><td class="n">8.0</td>
<td>20:00–24:00 daily</td></tr>
<tr><td>Storage holding load</td><td class="n">1,490</td><td class="n">2.3</td><td>all day</td></tr>
</table>
<div class="box">Pre-freeze runs every night of the period, only within 20:00–24:00 (after the
last inbound truck, before the midnight maintenance window).</div>"""),
    {"earliest_hour": 20, "deadline_hour": 24, "energy_mwh": 24, "max_mw": 8,
     "how": "720 MWh / 30 days = 24 MWh per day; 8.0 MW; 20:00 to 24:00"})

vans = [0] * 24
for h, v in {1: 1.5, 2: 1.4, 3: 1.2, 4: 0.8, 5: 0.5}.items():
    vans[h] = v
add("load_chart_03", "charging load chart (delivery vans)", page(f"""
<h1>Harbor Street Grocers — Delivery van charging</h1>
<div class="muted">Overnight charging, last week average (MW)</div>
<div style="margin-top:10px">{bars(vans, labels=("charging", ""),
                               title="Van charging by hour (MW)", unit="MW")}</div>
<div class="box">Daily charging energy: <b>5.4 MWh</b> (area under the bars). Site limit for
the chargers: <b>1.5 MW</b>. Vans are plugged in from 01:00 (after the night shift) and
leave at 06:00.</div>"""),
    {"earliest_hour": 1, "deadline_hour": 6, "energy_mwh": 5.4, "max_mw": 1.5,
     "how": "stated 5.4 MWh and 1.5 MW; 01:00 to 06:00"})


def main() -> None:
    SRC.mkdir(parents=True, exist_ok=True)
    truth = {}
    for d in DOCS:
        stem = d["file"].removesuffix(".png")
        (SRC / f"{stem}.html").write_text(d["html"])
        t = d["truth"]
        assert t["energy_mwh"] <= t["max_mw"] * (t["deadline_hour"] - t["earliest_hour"]) + 1e-9
        truth[d["file"]] = {"kind": d["kind"], **t}
    (OUT / "ground_truth.json").write_text(json.dumps(truth, indent=2) + "\n")
    print(f"wrote {len(DOCS)} documents to {SRC} and ground_truth.json")


if __name__ == "__main__":
    main()
