"""Server-rendered SVG time-series chart that never draws across missing data.

* The line breaks wherever two received readings are more than 1.5 x the expected interval
  apart, and at every reading that was not accepted.
* Gaps are shaded and labelled "no data". If the newest reading is older than the stale limit,
  the time from it until now is shaded too, so stale data never looks current.
* Rejected raw values are drawn as orange x marks (or a triangle on the edge when off-scale).
No JavaScript library or CDN is needed, so the dashboard works offline.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from html import escape

from monitor.history_m6 import parse_ts

INK, GRID, LINE, GAP, BAD, ON, OFF = "#1d2733", "#d5dbe1", "#24527a", "#c8372d", "#b26a00", "#b3261e", "#8a4b46"


def _ticks(lo, hi, n=5):
    """'Nice' axis ticks (1, 2, 2.5, 5 x 10^k) covering lo..hi."""
    span = hi - lo
    if span <= 0:
        return [lo]
    raw = span / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    v = math.ceil(lo / step) * step
    out = []
    while v <= hi + 1e-9:
        out.append(round(v, 6))
        v += step
    return out


def _time_label(t: datetime, span_s: float) -> str:
    if span_s <= 2 * 3600:
        return t.strftime("%H:%M:%S")
    if span_s <= 2 * 86400:
        return t.strftime("%H:%M")
    return t.strftime("%m-%d %H:%M")


def timeseries_svg(rows, interval_s, *, thresholds=(), start=None, end=None, stale_after_s=None,
                   width=960, height=290, unit="°C", gap_multiplier=1.5, title=""):
    rows = sorted(rows, key=lambda r: (r["timestamp"], r.get("id", 0)))
    # only numeric raw values can be drawn; text from versions before the 7.2 fix is skipped
    received = [(parse_ts(r["timestamp"]), r) for r in rows
                if isinstance(r["raw_value"], (int, float)) and not isinstance(r["raw_value"], bool)]
    left, right, top, bottom = 56, 18, 26, 34
    pw, ph = width - left - right, height - top - bottom
    head = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" '
            f'aria-label="{escape(title or "time series")}" font-family="system-ui, -apple-system, Segoe UI, sans-serif" '
            f'font-size="12">')
    if not received:
        return head + (f'<rect x="0" y="0" width="{width}" height="{height}" fill="#f7f9fb"/>'
                       f'<text x="{width / 2}" y="{height / 2}" text-anchor="middle" fill="{INK}">'
                       f'No readings in this window</text></svg>')

    t0 = start or received[0][0]
    t1 = end or received[-1][0]
    if t1 <= t0:
        t1 = t0 + timedelta(seconds=max(interval_s, 1))
    span = (t1 - t0).total_seconds()
    acc = [r["accepted_value"] for _, r in received if r["accepted_value"] is not None]
    yvals = (acc + [t[0] for t in thresholds]) if acc else [r["raw_value"] for _, r in received]
    ylo, yhi = min(yvals), max(yvals)
    pad = max((yhi - ylo) * 0.12, 0.2)
    ylo, yhi = ylo - pad, yhi + pad

    def x(t):
        return left + pw * ((t - t0).total_seconds() / span)

    def y(v):
        return top + ph * (1 - (v - ylo) / (yhi - ylo))

    out = [head, f'<rect x="{left}" y="{top}" width="{pw}" height="{ph}" fill="#ffffff" stroke="{GRID}"/>']
    for v in _ticks(ylo, yhi, 5):
        out.append(f'<line x1="{left}" x2="{left + pw}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="{GRID}" stroke-width="0.6"/>'
                   f'<text x="{left - 6}" y="{y(v) + 4:.1f}" text-anchor="end" fill="{INK}">{v:g}</text>')
    for i in range(6):
        t = t0 + timedelta(seconds=span * i / 5)
        anchor = "start" if i == 0 else "end" if i == 5 else "middle"
        out.append(f'<line x1="{x(t):.1f}" x2="{x(t):.1f}" y1="{top + ph}" y2="{top + ph + 4}" stroke="{INK}"/>'
                   f'<text x="{x(t):.1f}" y="{top + ph + 17}" text-anchor="{anchor}" fill="{INK}">{_time_label(t, span)}</text>')
    out.append(f'<text x="14" y="{top + ph / 2}" transform="rotate(-90 14 {top + ph / 2})" text-anchor="middle" '
               f'fill="{INK}">{escape(unit)}</text>')

    # gaps between received readings, and the trailing stale period
    gap_s = gap_multiplier * interval_s
    gaps = [(a, b) for (a, _), (b, _) in zip(received, received[1:]) if (b - a).total_seconds() > gap_s]
    if stale_after_s is not None and end is not None and (end - received[-1][0]).total_seconds() > stale_after_s:
        gaps.append((received[-1][0], end))
    for a, b in gaps:
        xa, xb = max(x(a), left), min(x(b), left + pw)
        out.append(f'<rect x="{xa:.1f}" y="{top}" width="{max(xb - xa, 1.5):.1f}" height="{ph}" fill="{GAP}" '
                   f'fill-opacity="0.13"><title>no data {a:%H:%M:%S} - {b:%H:%M:%S}</title></rect>')
        if xb - xa > 34:
            out.append(f'<text x="{(xa + xb) / 2:.1f}" y="{top + 14}" text-anchor="middle" fill="{GAP}" '
                       f'font-size="11">no data</text>')

    for value, label, kind in thresholds:
        if ylo <= value <= yhi:
            color, dash = (ON, "6 4") if kind == "on" else (OFF, "2 4")
            out.append(f'<line x1="{left}" x2="{left + pw}" y1="{y(value):.1f}" y2="{y(value):.1f}" '
                       f'stroke="{color}" stroke-width="1.2" stroke-dasharray="{dash}"/>'
                       f'<text x="{left + 6}" y="{y(value) - 4:.1f}" text-anchor="start" fill="{color}" '
                       f'font-size="11" stroke="#ffffff" stroke-width="3" paint-order="stroke">{escape(label)}</text>')

    # line segments: break at gaps and at non-accepted readings
    segments, cur, prev_t = [], [], None
    for t, r in received:
        if prev_t is not None and (t - prev_t).total_seconds() > gap_s:
            segments.append(cur)
            cur = []
        if r["accepted_value"] is None:
            segments.append(cur)
            cur = []
        else:
            cur.append((t, r["accepted_value"]))
        prev_t = t
    segments.append(cur)
    for seg in segments:
        if len(seg) == 1:
            t, v = seg[0]
            out.append(f'<circle cx="{x(t):.1f}" cy="{y(v):.1f}" r="1.8" fill="{LINE}"/>')
        elif seg:
            pts = " ".join(f"{x(t):.1f},{y(v):.1f}" for t, v in seg)
            out.append(f'<polyline points="{pts}" fill="none" stroke="{LINE}" stroke-width="1.6" '
                       f'stroke-linejoin="round"/>')

    for t, r in received:
        if r["accepted_value"] is None:
            v = r["raw_value"]
            cx = x(t)
            if v > yhi or v < ylo:
                edge = top + 6 if v > yhi else top + ph - 6
                tip = edge - 5 if v > yhi else edge + 5
                out.append(f'<path d="M{cx - 5:.1f},{edge:.1f} L{cx + 5:.1f},{edge:.1f} L{cx:.1f},{tip:.1f} Z" '
                           f'fill="{BAD}"><title>{escape(r["status"])}: {v:g} (off scale)</title></path>')
            else:
                cy = y(v)
                out.append(f'<path d="M{cx - 4:.1f},{cy - 4:.1f} L{cx + 4:.1f},{cy + 4:.1f} M{cx - 4:.1f},{cy + 4:.1f} '
                           f'L{cx + 4:.1f},{cy - 4:.1f}" stroke="{BAD}" stroke-width="1.8">'
                           f'<title>{escape(r["status"])}: {v:g}</title></path>')
    out.append("</svg>")
    return "".join(out)
