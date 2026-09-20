import json
import math
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
OUT = RESULTS / "charts"

TOPICS = ["shipping", "semiconductors", "energy"]
TOPIC_TITLES = {
    "shipping": "Shipping disruptions",
    "semiconductors": "Semiconductor supply chains",
    "energy": "Energy infrastructure",
}
LANGUAGES = ["english", "spanish", "chinese"]
COLORS = {
    "english": "#2563eb",
    "spanish": "#d97706",
    "chinese": "#059669",
}

W, H = 1100, 480
ML, MR, MT, MB = 70, 30, 80, 100
PX0, PX1 = ML, W - MR
PY0, PY1 = MT, H - MB

CAPTION = ("Matches per 10,000 same-language monitored articles; "
           "not unique stories. UTC daily bins. Gaps are not zero.")
SUBTITLE = "No publisher-time or lead-time claim"
UNAVAIL = "Normalization unavailable: comparison withheld"

STATE_FILL = {
    "observed": "#059669",
    "excluded_latest_bin": "#d97706",
    "missing": "#dc2626",
    "unverified_denominator": "#9ca3af",
}


def fmt_date(d):
    if len(d) >= 8 and d[:8].isdigit():
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return escape(d)


def x_pos(date, dates):
    if len(dates) == 1:
        return (PX0 + PX1) / 2
    return PX0 + (PX1 - PX0) * dates.index(date) / (len(dates) - 1)


def y_pos(v, ymax):
    return PY1 - (PY1 - PY0) * v / ymax


def render_topic(topic, rows, dates, ymax, title=None, caption=CAPTION,
                 subtitle=SUBTITLE, languages=LANGUAGES,
                 unavailable=UNAVAIL):
    e = []
    a = e.append
    a(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
      f'viewBox="0 0 {W} {H}" font-family="Helvetica, Arial, sans-serif">')
    a(f'<rect x="0" y="0" width="{W}" height="{H}" fill="#ffffff"/>')
    a(f'<text x="{W/2}" y="34" text-anchor="middle" font-size="22" '
      f'font-weight="bold" fill="#111827">'
      f'{escape(title or TOPIC_TITLES[topic])}</text>')
    a(f'<text x="{W/2}" y="56" text-anchor="middle" font-size="13" '
      f'font-style="italic" fill="#6b7280">{escape(subtitle)}</text>')

    for i in range(5):
        v = ymax * i / 4
        y = y_pos(v, ymax)
        a(f'<line x1="{PX0}" y1="{y:.2f}" x2="{PX1}" y2="{y:.2f}" '
          f'stroke="#e5e7eb" stroke-width="1"/>')
        label = f"{v:.0f}" if v == int(v) else f"{v:.1f}"
        a(f'<text x="{PX0-8}" y="{y+4:.2f}" text-anchor="end" font-size="11" '
          f'fill="#374151">{label}</text>')

    step = max(1, math.ceil(len(dates) / 6))
    for i, d in enumerate(dates):
        if i % step != 0 and i != len(dates) - 1:
            continue
        x = x_pos(d, dates)
        a(f'<text x="{x:.2f}" y="{PY1+14}" text-anchor="end" font-size="10" '
          f'fill="#374151" transform="rotate(-35 {x:.2f} {PY1+14})">'
          f'{fmt_date(d)}</text>')

    observed_dates = {r["date"] for r in rows
                      if r["state"] == "observed"
                      and r.get("per_10k_language_articles") is not None}
    tail_start = None
    for d in reversed(dates):
        if d in observed_dates:
            break
        tail_start = d
    if tail_start is not None:
        i = dates.index(tail_start)
        x0 = x_pos(tail_start, dates) if i == 0 else (
            x_pos(dates[i - 1], dates) + x_pos(tail_start, dates)) / 2
        a(f'<rect x="{x0:.2f}" y="{PY0}" width="{PX1-x0:.2f}" '
          f'height="{PY1-PY0}" fill="#9ca3af" fill-opacity="0.15"/>')
        a(f'<text x="{(x0+PX1)/2:.2f}" y="{PY0+16}" text-anchor="middle" '
          f'font-size="10" fill="#6b7280">excluded / missing</text>')

    any_share = False
    diamond_labeled = False
    for lang in languages:
        color = COLORS[lang]
        seg = []
        for r in sorted((r for r in rows if r["language"] == lang),
                        key=lambda r: dates.index(r["date"])):
            v = r.get("per_10k_language_articles")
            x = x_pos(r["date"], dates)
            if r["state"] == "observed" and v is not None:
                any_share = True
                y = y_pos(v, ymax)
                seg.append((x, y))
                if r.get("candidate"):
                    a(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="4.5" '
                      f'fill="#ffffff" stroke="{color}" stroke-width="2"/>')
                    a(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="2" '
                      f'fill="{color}"/>')
            else:
                if len(seg) > 1:
                    pts = " ".join(f"{px:.2f},{py:.2f}" for px, py in seg)
                    a(f'<polyline points="{pts}" fill="none" '
                      f'stroke="{color}" stroke-width="2"/>')
                elif len(seg) == 1:
                    px, py = seg[0]
                    a(f'<circle cx="{px:.2f}" cy="{py:.2f}" r="3" '
                      f'fill="{color}"/>')
                seg = []
                if r["state"] == "excluded_latest_bin" and v is not None:
                    y = y_pos(v, ymax)
                    a(f'<rect x="{x-4:.2f}" y="{y-4:.2f}" width="8" height="8" '
                      f'fill="#ffffff" stroke="{color}" stroke-width="1.5" '
                      f'transform="rotate(45 {x:.2f} {y:.2f})"/>')
                    lbl = "excluded/provisional" if not diamond_labeled else ""
                    if lbl:
                        a(f'<text x="{x:.2f}" y="{y-10:.2f}" '
                          f'text-anchor="middle" font-size="10" '
                          f'fill="{color}">{lbl}</text>')
                        diamond_labeled = True
        if len(seg) > 1:
            pts = " ".join(f"{px:.2f},{py:.2f}" for px, py in seg)
            a(f'<polyline points="{pts}" fill="none" stroke="{color}" '
              f'stroke-width="2"/>')
        elif len(seg) == 1:
            px, py = seg[0]
            a(f'<circle cx="{px:.2f}" cy="{py:.2f}" r="3" fill="{color}"/>')

    if not any_share:
        a(f'<text x="{W/2}" y="{(PY0+PY1)/2}" text-anchor="middle" '
          f'font-size="20" font-weight="bold" fill="#b91c1c">'
          f'{escape(unavailable)}</text>')

    lx = PX0
    ly = PY1 + 62
    for lang in languages:
        a(f'<rect x="{lx}" y="{ly-10}" width="14" height="4" '
          f'fill="{COLORS[lang]}"/>')
        a(f'<circle cx="{lx+7}" cy="{ly-8}" r="3" fill="{COLORS[lang]}"/>')
        a(f'<text x="{lx+20}" y="{ly-4}" font-size="12" '
          f'fill="#374151">{lang}</text>')
        lx += 110
    a(f'<rect x="{lx}" y="{ly-12}" width="9" height="9" fill="#ffffff" '
      f'stroke="#6b7280" stroke-width="1.5" '
      f'transform="rotate(45 {lx+4.5} {ly-7.5})"/>')
    a(f'<text x="{lx+18}" y="{ly-4}" font-size="12" fill="#374151">'
      f'excluded/provisional</text>')
    lx += 160
    a(f'<circle cx="{lx+4}" cy="{ly-8}" r="4" fill="#ffffff" '
      f'stroke="#374151" stroke-width="2"/>')
    a(f'<text x="{lx+14}" y="{ly-4}" font-size="12" '
      f'fill="#374151">candidate</text>')

    a(f'<text x="{W/2}" y="{H-8}" text-anchor="middle" font-size="11" '
      f'fill="#6b7280">{escape(caption)}</text>')
    a('</svg>')
    return "\n".join(e)


def render_availability(rows, dates):
    aw, ah = 1100, 560
    top, left, right, bottom = 70, 170, 20, 130
    gx0, gx1 = left, aw - right
    gy0, gy1 = top, ah - bottom
    cw = (gx1 - gx0) / max(1, len(dates))
    rh = (gy1 - gy0) / (len(TOPICS) * len(LANGUAGES))

    state_by = {(r["topic"], r["language"], r["date"]): r["state"]
                for r in rows}
    e = []
    a = e.append
    a(f'<svg xmlns="http://www.w3.org/2000/svg" width="{aw}" height="{ah}" '
      f'viewBox="0 0 {aw} {ah}" font-family="Helvetica, Arial, sans-serif">')
    a(f'<rect x="0" y="0" width="{aw}" height="{ah}" fill="#ffffff"/>')
    a(f'<text x="{aw/2}" y="34" text-anchor="middle" font-size="20" '
      f'font-weight="bold" fill="#111827">Data availability by day</text>')
    a(f'<text x="{aw/2}" y="54" text-anchor="middle" font-size="13" '
      f'font-style="italic" fill="#6b7280">{escape(SUBTITLE)}</text>')

    row_i = 0
    for topic in TOPICS:
        for lang in LANGUAGES:
            y = gy0 + row_i * rh
            a(f'<text x="{gx0-8}" y="{y+rh/2+4:.2f}" text-anchor="end" '
              f'font-size="11" fill="#374151">'
              f'{escape(topic)} / {escape(lang)}</text>')
            for di, d in enumerate(dates):
                st = state_by.get((topic, lang, d), "missing")
                fill = STATE_FILL.get(st, "#9ca3af")
                x = gx0 + di * cw
                a(f'<rect x="{x:.2f}" y="{y:.2f}" width="{cw-1:.2f}" '
                  f'height="{rh-1:.2f}" fill="{fill}"/>')
            row_i += 1

    step = max(1, math.ceil(len(dates) / 12))
    for i, d in enumerate(dates):
        if i % step != 0 and i != len(dates) - 1:
            continue
        x = gx0 + i * cw + cw / 2
        a(f'<text x="{x:.2f}" y="{gy1+12}" text-anchor="end" font-size="9" '
          f'fill="#374151" transform="rotate(-45 {x:.2f} {gy1+12})">'
          f'{fmt_date(d)}</text>')

    lx = gx0
    ly = ah - 22
    for st, label in [("observed", "observed"),
                      ("excluded_latest_bin", "excluded_latest_bin"),
                      ("missing", "missing"),
                      ("unverified_denominator", "unverified_denominator")]:
        a(f'<rect x="{lx}" y="{ly-10}" width="12" height="12" '
          f'fill="{STATE_FILL[st]}"/>')
        a(f'<text x="{lx+16}" y="{ly}" font-size="11" '
          f'fill="#374151">{label}</text>')
        lx += 40 + 7 * len(label)
    a('</svg>')
    return "\n".join(e)


def main():
    rows = json.loads((RESULTS / "timeline.json").read_text())
    dates = sorted({r["date"] for r in rows})

    vals = [r["per_10k_language_articles"] for r in rows
            if r.get("per_10k_language_articles") is not None]
    ymax = max(10, math.ceil(max(vals, default=1) / 10) * 10)

    OUT.mkdir(parents=True, exist_ok=True)
    for topic in TOPICS:
        trows = [r for r in rows if r["topic"] == topic]
        svg = render_topic(topic, trows, dates, ymax)
        (OUT / f"{topic}.svg").write_text(svg)
    (OUT / "availability.svg").write_text(render_availability(rows, dates))
    for topic in TOPICS:
        for lang in LANGUAGES:
            group = [r for r in rows if r['topic'] == topic and r['language'] == lang]
            counts = [r['article_count'] for r in group if r.get('article_count') is not None]
            if not counts:
                continue
            display_rows = [{**r, 'per_10k_language_articles': r['article_count'],
                             'state': r.get('raw_state', 'missing'),
                             'candidate': r.get('raw_candidate', False)} for r in group]
            raw_ymax = max(10, math.ceil(max(counts) / 10) * 10)
            svg = render_topic(topic, display_rows, dates, raw_ymax,
                title=TOPIC_TITLES[topic] + ' / ' + lang + ' — raw counts',
                caption='Aggregate API article counts, NOT ArticleList lengths. Within-language audit only; not cross-language comparable.',
                subtitle='No exposure correction. Gaps are not zero. No lead-time claim.',
                languages=[lang], unavailable='Raw aggregate timeline unavailable')
            (OUT / f'raw_{topic}_{lang}.svg').write_text(svg)
    print(f"wrote charts to {OUT}")


MSC_DIR = RESULTS / "msc_validation"
MSC_W, MSC_H = 1200, 820
MSC_X0, MSC_X1 = 180, 1120
MSC_TICKS = ["2026-08-25", "2026-08-29", "2026-09-02", "2026-09-06",
             "2026-09-10", "2026-09-14", "2026-09-19"]
ROLE_LABELS = {
    "direct_suspension": "direct suspension",
    "roundup_suspension": "roundup suspension",
}


def render_msc_timeline():
    from datetime import date

    data = json.loads((MSC_DIR / "timeline_data.json").read_text())
    start = date.fromisoformat(data["start_date"])
    end = date.fromisoformat(data["end_date"])
    span = (end - start).days

    def xp(dstr):
        d = date.fromisoformat(dstr)
        return MSC_X0 + (MSC_X1 - MSC_X0) * (d - start).days / span

    lane_ys = [210, 330, 450]
    e = []
    a = e.append
    a(f'<svg xmlns="http://www.w3.org/2000/svg" width="{MSC_W}" '
      f'height="{MSC_H}" viewBox="0 0 {MSC_W} {MSC_H}" '
      f'font-family="Helvetica, Arial, sans-serif">')
    a(f'<rect x="0" y="0" width="{MSC_W}" height="{MSC_H}" fill="#ffffff"/>')
    a(f'<text x="{MSC_W/2}" y="34" text-anchor="middle" font-size="24" '
      f'font-weight="bold" fill="#b91c1c">INCOMPLETE EVIDENCE-ONLY REPLAY</text>')
    a(f'<text x="{MSC_W/2}" y="60" text-anchor="middle" font-size="16" '
      f'fill="#111827">{escape(data["title"])}</text>')
    a(f'<text x="{MSC_W/2}" y="84" text-anchor="middle" font-size="14" '
      f'font-weight="bold" fill="#6b7280">Publisher-claimed dates, not first '
      f'publication or real-time observation</text>')

    for i, ev in enumerate(data["events"]):
        y = lane_ys[i]
        a(f'<rect x="{MSC_X0}" y="{y-40}" width="{MSC_X1-MSC_X0}" height="80" '
          f'fill="#f3f4f6"/>')
        a(f'<text x="20" y="{y+4}" font-size="13" fill="#111827">'
          f'{escape(ev["publisher"])}</text>')
        a(f'<text x="20" y="{y+22}" font-size="13" fill="#111827">'
          f'{escape(ev["language"])}</text>')

    for t in MSC_TICKS:
        x = xp(t)
        a(f'<line x1="{x:.2f}" y1="140" x2="{x:.2f}" y2="520" '
          f'stroke="#e5e7eb" stroke-width="1"/>')
        a(f'<text x="{x:.2f}" y="540" text-anchor="middle" font-size="12" '
          f'fill="#374151">{t}</text>')

    cx = xp(data["doc_cutoff_date"])
    a(f'<line x1="{cx:.2f}" y1="110" x2="{cx:.2f}" y2="520" '
      f'stroke="#6b7280" stroke-width="1.5" stroke-dasharray="6,4"/>')
    a(f'<text x="{cx:.2f}" y="104" text-anchor="middle" font-size="12" '
      f'fill="#374151">Sep 13: prior broad DOC endpoint '
      f'(not a story cutoff)</text>')

    for i, ev in enumerate(data["events"]):
        y = lane_ys[i]
        x = xp(ev["display_date"])
        claimed = ev.get("publisher_publication_utc_claim")
        role = ROLE_LABELS.get(ev.get("role"), ev.get("role", ""))
        when = claimed if claimed else (
            ev['display_date'] + ' (date only; time/timezone unknown)')
        a(f'<a href="{escape(ev["url"], quote=True)}">')
        if claimed:
            a(f'<circle cx="{x:.2f}" cy="{y}" r="7" fill="#2563eb"/>')
        else:
            a(f'<rect x="{x-6:.2f}" y="{y-6}" width="12" height="12" '
              f'fill="#ffffff" stroke="#d97706" stroke-width="2" '
              f'transform="rotate(45 {x:.2f} {y})"/>')
        ax = max(MSC_X0, x - 30)
        a(f'<text x="{ax:.2f}" y="{y+22}" font-size="12" fill="#111827">'
          f'{escape(ev["publisher"])}</text>')
        a(f'<text x="{ax:.2f}" y="{y+37}" font-size="12" fill="#374151">'
          f'{escape(role)}</text>')
        a(f'<text x="{ax:.2f}" y="{y+52}" font-size="12" fill="#6b7280">'
          f'{escape(when)}</text>')
        a('</a>')

    ly = 585
    a(f'<circle cx="{MSC_X0+6}" cy="{ly-4}" r="7" fill="#2563eb"/>')
    a(f'<text x="{MSC_X0+20}" y="{ly}" font-size="13" fill="#374151">'
      f'publisher metadata with claimed UTC time</text>')
    a(f'<rect x="{MSC_X0+480}" y="{ly-11}" width="12" height="12" '
      f'fill="#ffffff" stroke="#d97706" stroke-width="2" '
      f'transform="rotate(45 {MSC_X0+486} {ly-5})"/>')
    a(f'<text x="{MSC_X0+500}" y="{ly}" font-size="13" fill="#374151">'
      f'date-only page; time/timezone unknown</text>')
    a(f'<text x="{MSC_X0}" y="{ly+30}" font-size="13" font-weight="bold" '
      f'fill="#374151">Blank intervals = unavailable/unsearched, NOT zero '
      f'coverage.</text>')
    a(f'<text x="{MSC_X0}" y="{ly+52}" font-size="13" fill="#374151">'
      f'All three pages captured September 19; GDELT observation times for '
      f'these URLs remain unavailable.</text>')

    fy = ly + 95
    for line in [
        'Exact URLs deduplicated. Shared upstream reporting remains possible; '
        'do not assume three independent origins.',
        'Carrier notice and upstream originals unverified. Vessel direction '
        'and attack-date details remain uncertain.',
        'No normalized comparison, first-publication claim, language lead, '
        'or early-warning result.',
    ]:
        a(f'<text x="{MSC_W/2}" y="{fy}" text-anchor="middle" font-size="13" '
          f'fill="#6b7280">{escape(line)}</text>')
        fy += 22

    a('</svg>')
    out = MSC_DIR / "story_timeline.svg"
    out.write_text("\n".join(e))
    print(f"wrote {out}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--msc", action="store_true")
    args = parser.parse_args()
    if args.msc:
        render_msc_timeline()
    else:
        main()
