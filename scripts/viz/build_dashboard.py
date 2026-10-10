#!/usr/bin/env python
"""Build docs/data/dashboard.html: one self-contained page (data inlined, no external scripts)
with the session timeline, per-question stats, query latency, compression and the
question-type finding. For the team and the Tiger Data judges.

    python scripts/viz/build_dashboard.py [--fragment PATH]

Inputs: frontend/src/mocks/replay_sample.json and docs/data/samples/{bench_latency,
compression,stress_by_question_type}.csv. --fragment also writes the page without the
<html>/<head>/<body> wrapper (for publishing as a hosted page).
"""
import argparse
import csv
import json
from pathlib import Path

import style

ROOT = style.ROOT


def read_csv(name: str) -> list[dict]:
    with (style.SAMPLES / name).open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_data() -> dict:
    replay = json.loads((ROOT / "frontend/src/mocks/replay_sample.json").read_text(encoding="utf-8"))
    lat = [{"query": r["query"], "caller": r["caller"], "p50": num(r["p50_ms"]), "p95": num(r["p95_ms"]),
            "server": num(r["server_exec_ms"])} for r in read_csv("bench_latency.csv")]
    comp = [{"dataset": r["dataset"], "sessions": int(r["sessions"]), "rows": int(r["rows"]),
             "row_bytes": int(r["rowstore_bytes"]), "col_bytes": int(r["columnstore_bytes"]), "ratio": num(r["ratio"]),
             "identical": r["rollup_identical"] == "True"} for r in read_csv("compression.csv")]
    qt = [{"interview_type": r["interview_type"] or None, "kind": r["kind"] or None, "questions": int(r["questions"]),
           "high": int(r["high_peaks"]), "peak": num(r["stress_peak"]), "mean": num(r["stress_mean"])}
          for r in read_csv("stress_by_question_type.csv")]
    return {"replay": replay, "latency": lat, "compression": comp, "qtypes": qt}


CSS = r"""
/* Layout: one reading column of panels; tiles wrap; latency + compression sit side by side when wide. */
:root {
  --bg: #f9f9f7; --panel: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --wash: #f0efec; --line: rgba(11,11,11,0.10);
  --mean: #256abf; --peak: #86b6ef; --bar-muted: #c3c2b7;
  --calm: #006300; --rising: #8a5a00; --high: #b42318;
  --sans: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  --mono: ui-monospace, "Cascadia Mono", "SF Mono", Consolas, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #0d0d0d; --panel: #1a1a19; --ink: #f0efec; --ink-2: #c3c2b7; --muted: #898781;
  --grid: #2c2c2a; --axis: #383835; --wash: #24241f; --line: rgba(255,255,255,0.10);
  --mean: #6da7ec; --peak: #1c5cab; --bar-muted: #52514e;
  --calm: #0ca30c; --rising: #fab219; --high: #e66767; color-scheme: dark; } }
:root[data-theme="dark"] {
  --bg: #0d0d0d; --panel: #1a1a19; --ink: #f0efec; --ink-2: #c3c2b7; --muted: #898781;
  --grid: #2c2c2a; --axis: #383835; --wash: #24241f; --line: rgba(255,255,255,0.10);
  --mean: #6da7ec; --peak: #1c5cab; --bar-muted: #52514e;
  --calm: #0ca30c; --rising: #fab219; --high: #e66767; color-scheme: dark; }
body { background: var(--bg); color: var(--ink); font-family: var(--sans); margin: 0; }
.wrap { max-width: 1120px; margin: 0 auto; padding-inline: 16px; padding-block: 28px 48px; display: grid; gap: 20px; }
header h1 { font-size: 1.6rem; margin: 0 0 4px; text-wrap: balance; letter-spacing: -0.01em; }
header p { margin: 0; color: var(--ink-2); max-width: 70ch; line-height: 1.5; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; }
.tile { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; display: grid; gap: 4px; }
.tile .label { font-size: 0.78rem; color: var(--ink-2); letter-spacing: 0.02em; }
.tile .value { font-size: 1.7rem; font-weight: 600; }
.tile .note { font-size: 0.78rem; color: var(--muted); line-height: 1.35; }
.panel { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 16px; display: grid; gap: 10px; min-width: 0; }
.panel h2 { font-size: 1.02rem; margin: 0; }
.panel .sub { font-size: 0.85rem; color: var(--ink-2); margin: 0; line-height: 1.45; }
.pair { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 20px; }
.chart { position: relative; width: 100%; min-width: 0; }
.chart svg { display: block; width: 100%; height: auto; overflow: visible; }
.chart text { fill: var(--ink-2); font-family: var(--sans); }
.legend { display: flex; flex-wrap: wrap; gap: 14px; font-size: 0.8rem; color: var(--ink-2); }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.key { width: 16px; height: 3px; border-radius: 2px; display: inline-block; }
.dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; }
.tip { position: absolute; pointer-events: none; background: var(--panel); color: var(--ink); border: 1px solid var(--line);
  box-shadow: 0 4px 14px rgba(0,0,0,0.12); border-radius: 8px; padding: 8px 10px; font-size: 0.8rem; line-height: 1.45;
  white-space: nowrap; z-index: 2; }
.tip b { font-weight: 600; }
.tablewrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 0.84rem; font-variant-numeric: tabular-nums; }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { color: var(--ink-2); font-weight: 600; font-size: 0.76rem; letter-spacing: 0.02em; white-space: nowrap; }
td.n, th.n { text-align: right; white-space: nowrap; }
td.q { min-width: 220px; max-width: 420px; }
tr.sel td { background: var(--wash); }
.chip { display: inline-flex; align-items: center; gap: 5px; font-size: 0.76rem; font-weight: 600; white-space: nowrap; }
.chip::before { content: ""; width: 8px; height: 8px; border-radius: 50%; background: currentColor; }
.chip.calm { color: var(--calm); } .chip.rising { color: var(--rising); } .chip.high { color: var(--high); }
.mono { font-family: var(--mono); font-size: 0.8rem; }
footer { color: var(--muted); font-size: 0.8rem; line-height: 1.5; }
@media (prefers-reduced-motion: no-preference) { tr { transition: background 0.15s; } }
"""

JS = r"""
const D = JSON.parse(document.getElementById('data').textContent);
const NS = 'http://www.w3.org/2000/svg';
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const el = (tag, attrs = {}, parent) => { const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); if (parent) parent.appendChild(e); return e; };
const txt = (parent, x, y, s, attrs = {}) => { const t = el('text', {x, y, 'font-size': 11, ...attrs}, parent); t.textContent = s; return t; };
const fmtT = s => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
const level = s => s == null ? null : s > 70 ? 'high' : s >= 40 ? 'rising' : 'calm';
const qLabel = q => q.kind === 'calibration' ? 'Cal' : `Q${q.idx}` + (q.kind === 'follow_up' ? '+' : '');
function tipAt(box, html, x, y) {
  let t = box.querySelector('.tip'); if (!t) { t = document.createElement('div'); t.className = 'tip'; box.appendChild(t); }
  t.innerHTML = html; t.hidden = false;
  const w = t.offsetWidth, bw = box.clientWidth;
  t.style.left = Math.max(0, Math.min(bw - w, x + 14)) + 'px'; t.style.top = Math.max(0, y - 10) + 'px';
}
const hideTip = box => { const t = box.querySelector('.tip'); if (t) t.hidden = true; };

/* ---------- session timeline ---------- */
function timeline() {
  const box = document.getElementById('timeline'); box.querySelector('svg')?.remove();
  const R = D.replay, t0 = Date.parse(R.session.started_at);
  const pts = R.series.map(p => ({...p, s: (Date.parse(p.t) - t0) / 1000}));
  const qs = R.questions.filter(q => q.answer_started_at && q.answered_at).map(q => ({...q,
    a: (Date.parse(q.answer_started_at) - t0) / 1000, b: (Date.parse(q.answered_at) - t0) / 1000}));
  const W = Math.max(360, box.clientWidth), L = 40, Rm = 70, top = 26, h1 = 170, gap = 44, h2 = 110, H = top + h1 + gap + h2 + 30;
  const tMax = Math.max(...pts.map(p => p.s)) + 5;
  const x = s => L + (W - L - Rm) * s / tMax;
  const ys = v => top + h1 - h1 * v / 100;
  const hrs = pts.map(p => p.hr).filter(v => v != null), hLo = Math.floor(Math.min(...hrs, R.session.baseline?.heart_rate ?? 99) - 2),
        hHi = Math.ceil(Math.max(...hrs) + 2);
  const yh = v => top + h1 + gap + h2 - h2 * (v - hLo) / (hHi - hLo);
  const svg = el('svg', {viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': 'Stress and heart rate over the session'}, box);
  for (const q of qs) {
    el('rect', {x: x(q.a), y: top, width: Math.max(1, x(q.b) - x(q.a)), height: h1 + gap + h2, fill: css('--wash')}, svg);
    txt(svg, (x(q.a) + x(q.b)) / 2, top - 8, qLabel(q), {'text-anchor': 'middle', 'font-size': 10.5});
  }
  for (const v of [0, 40, 70, 100]) {
    el('line', {x1: L, x2: W - Rm, y1: ys(v), y2: ys(v), stroke: v === 40 || v === 70 ? css('--axis') : css('--grid'), 'stroke-width': 1}, svg);
    txt(svg, L - 6, ys(v) + 4, v, {'text-anchor': 'end'});
  }
  txt(svg, W - Rm + 6, ys(40) + 4, '40 rising'); txt(svg, W - Rm + 6, ys(70) + 4, '70 high');
  txt(svg, L, top + h1 + gap - 8, 'Heart rate (bpm)', {'font-weight': 600, fill: css('--ink')});
  for (const v of [hLo, Math.round((hLo + hHi) / 2), hHi]) txt(svg, L - 6, yh(v) + 4, v, {'text-anchor': 'end'});
  el('line', {x1: L, x2: W - Rm, y1: top + h1 + gap + h2, y2: top + h1 + gap + h2, stroke: css('--axis')}, svg);
  if (R.session.baseline) {
    const b = R.session.baseline.heart_rate;
    el('line', {x1: L, x2: W - Rm, y1: yh(b), y2: yh(b), stroke: css('--muted'), 'stroke-width': 1}, svg);
    txt(svg, W - Rm + 6, yh(b) + 4, `baseline ${Math.round(b)}`);
  }
  const step = (key, yf) => { let d = '', prev = null;
    pts.forEach((p, i) => { const v = p[key]; if (v == null) { prev = null; return; }
      const nx = x(i + 1 < pts.length ? pts[i + 1].s : p.s + 5);
      d += (prev == null ? `M${x(p.s)},${yf(v)}` : `L${x(p.s)},${yf(v)}`) + `L${nx},${yf(v)}`; prev = v; });
    return d; };
  el('path', {d: step('stress_peak', ys), fill: 'none', stroke: css('--peak'), 'stroke-width': 2, 'stroke-linejoin': 'round'}, svg);
  el('path', {d: step('stress', ys), fill: 'none', stroke: css('--mean'), 'stroke-width': 2, 'stroke-linejoin': 'round'}, svg);
  el('path', {d: step('hr', yh), fill: 'none', stroke: css('--mean'), 'stroke-width': 2, 'stroke-linejoin': 'round'}, svg);
  for (let s = 0; s <= tMax; s += 60) txt(svg, x(s), H - 8, fmtT(s), {'text-anchor': 'middle'});
  const cross = el('line', {y1: top, y2: top + h1 + gap + h2, stroke: css('--muted'), 'stroke-width': 1, visibility: 'hidden'}, svg);
  const hit = el('rect', {x: L, y: top, width: W - L - Rm, height: h1 + gap + h2, fill: 'transparent'}, svg);
  const move = ev => {
    const r = svg.getBoundingClientRect(), px = (ev.clientX - r.left) * W / r.width;
    const s = (px - L) / (W - L - Rm) * tMax; let i = pts.findIndex(p => p.s > s) - 1; if (i < 0) i = s < 0 ? 0 : pts.length - 1;
    const p = pts[i], q = qs.find(q => s >= q.a && s <= q.b);
    cross.setAttribute('x1', x(p.s + 2.5)); cross.setAttribute('x2', x(p.s + 2.5)); cross.setAttribute('visibility', 'visible');
    tipAt(box, `<b>${fmtT(p.s)}</b>${q ? ' &middot; ' + qLabel(q) : ''}<br>stress ${p.stress ?? '–'} (peak ${p.stress_peak ?? '–'})<br>` +
      `HR ${p.hr ?? '–'} bpm &middot; BR ${p.br ?? '–'} &middot; focus ${p.focus ?? '–'}`, (ev.clientX - box.getBoundingClientRect().left), (ev.clientY - box.getBoundingClientRect().top));
    document.querySelectorAll('#qtable tr[data-q]').forEach(tr => tr.classList.toggle('sel', q && tr.dataset.q === q.id));
  };
  hit.addEventListener('pointermove', move);
  hit.addEventListener('pointerleave', () => { cross.setAttribute('visibility', 'hidden'); hideTip(box);
    document.querySelectorAll('#qtable tr.sel').forEach(tr => tr.classList.remove('sel')); });
}

/* ---------- per-question table ---------- */
function qtable() {
  const tb = document.querySelector('#qtable tbody'); tb.innerHTML = '';
  for (const q of D.replay.questions) {
    const s = q.stats, lv = level(s.stress_peak), tr = document.createElement('tr'); tr.dataset.q = q.id;
    tr.innerHTML = `<td>${qLabel(q)}</td><td>${q.kind.replace('_', '-')}</td><td class="q">${q.text}</td>` +
      `<td class="n">${s.duration_s.toFixed(0)} s</td><td class="n">${s.hr_mean ?? '–'}</td>` +
      `<td class="n">${s.hr_delta_pct == null ? '–' : (s.hr_delta_pct > 0 ? '+' : '') + s.hr_delta_pct.toFixed(1) + '%'}</td>` +
      `<td class="n">${s.stress_mean ?? '–'}</td><td class="n">${s.stress_peak ?? '–'}</td>` +
      `<td>${lv ? `<span class="chip ${lv}">${lv}</span>` : '–'}</td>` +
      `<td class="n">${s.peak_offset_s == null ? '–' : s.peak_offset_s.toFixed(0) + ' s'}</td>`;
    tb.appendChild(tr);
  }
}

/* ---------- latency dot plot ---------- */
function latency() {
  const box = document.getElementById('latency'); box.querySelector('svg')?.remove();
  const floor = D.latency.find(r => r.query.startsWith('network floor'));
  const rows = D.latency.filter(r => r !== floor);
  const W = Math.max(320, box.clientWidth), L = Math.min(230, W * 0.46), Rm = 16, rh = 22, top = 30, H = top + rows.length * rh + 30;
  const xMax = Math.ceil(Math.max(...rows.map(r => r.p95)) / 20) * 20;
  const x = v => L + (W - L - Rm) * v / xMax;
  const svg = el('svg', {viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': 'Round-trip latency per query'}, box);
  el('rect', {x: x(0), y: top - 6, width: x(floor.p50) - x(0), height: rows.length * rh + 6, fill: css('--wash')}, svg);
  txt(svg, (x(0) + x(floor.p50)) / 2, top - 12, `network alone ${Math.round(floor.p50)} ms`, {'text-anchor': 'middle', 'font-size': 10});
  for (let v = 0; v <= xMax; v += 20) {
    el('line', {x1: x(v), x2: x(v), y1: top - 6, y2: top + rows.length * rh, stroke: css('--grid')}, svg);
    txt(svg, x(v), H - 10, v === xMax ? `${v} ms` : v, {'text-anchor': 'middle'});
  }
  el('line', {x1: x(50), x2: x(50), y1: top - 6, y2: top + rows.length * rh, stroke: css('--ink-2'), 'stroke-width': 1}, svg);
  txt(svg, x(50) + 4, top - 12, 'target 50', {'font-size': 10, fill: css('--ink')});
  rows.forEach((r, i) => {
    const y = top + i * rh + rh / 2;
    txt(svg, L - 8, y + 4, r.query.replace(/ \(.*\)/, ''), {'text-anchor': 'end', 'font-size': 10.5});
    el('line', {x1: x(r.p50), x2: x(r.p95), y1: y, y2: y, stroke: css('--axis'), 'stroke-width': 2}, svg);
    el('circle', {cx: x(r.p95), cy: y, r: 4.5, fill: css('--peak'), stroke: css('--panel'), 'stroke-width': 2}, svg);
    el('circle', {cx: x(r.p50), cy: y, r: 4.5, fill: css('--mean'), stroke: css('--panel'), 'stroke-width': 2}, svg);
    const hit = el('rect', {x: 0, y: y - rh / 2, width: W, height: rh, fill: 'transparent'}, svg);
    hit.addEventListener('pointermove', ev => { const b = box.getBoundingClientRect();
      tipAt(box, `<b>${r.query}</b><br>${r.caller}<br>round trip p50 ${r.p50} ms &middot; p95 ${r.p95} ms<br>` +
        `server ${r.server == null ? '1 row by primary key' : r.server.toFixed(2) + ' ms'}`, ev.clientX - b.left, ev.clientY - b.top); });
    hit.addEventListener('pointerleave', () => hideTip(box));
  });
}

/* ---------- horizontal bars (compression, question types) ---------- */
function bars(id, items, fmt, xMax, label) {
  const box = document.getElementById(id); box.querySelector('svg')?.remove();
  const W = Math.max(300, box.clientWidth), L = Math.min(180, W * 0.42), Rm = 110, rh = 30, H = items.length * rh + 10;
  const x = v => L + (W - L - Rm) * v / xMax;
  const svg = el('svg', {viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': label}, box);
  items.forEach((it, i) => {
    const y = 5 + i * rh, h = Math.min(18, rh - 10), w = Math.max(0, x(it.v) - x(0)), r = Math.min(4, w);
    txt(svg, L - 8, y + rh / 2 + 4, it.label, {'text-anchor': 'end', 'font-size': 11});
    el('path', {d: `M${x(0)},${y + (rh - h) / 2}h${w - r}a${r},${r} 0 0 1 ${r},${r}v${h - 2 * r}a${r},${r} 0 0 1 -${r},${r}h-${w - r}z`,
      fill: it.color}, svg);
    txt(svg, x(0) + w + 6, y + rh / 2 + 4, fmt(it), {'font-size': 11, fill: css('--ink')});
    const hit = el('rect', {x: 0, y, width: W, height: rh, fill: 'transparent'}, svg);
    hit.addEventListener('pointermove', ev => { const b = box.getBoundingClientRect(); tipAt(box, it.tip, ev.clientX - b.left, ev.clientY - b.top); });
    hit.addEventListener('pointerleave', () => hideTip(box));
  });
}
function compression() {
  const items = [];
  for (const c of D.compression) {
    const short = c.dataset.startsWith('+') ? 'with synthetic' : 'real sessions';
    // each pair on its own scale: rowstore = full width, columnstore = its share of it
    items.push({label: `${short}: rowstore`, v: 100, kib: c.row_bytes / 1024, color: css('--bar-muted'),
      tip: `<b>${c.dataset}</b><br>${c.sessions} sessions, ${c.rows.toLocaleString()} readings<br>rowstore ${(c.row_bytes / 1024).toFixed(0)} KiB (heap + indexes)`});
    items.push({label: `${short}: columnstore`, v: 100 * c.col_bytes / c.row_bytes, kib: c.col_bytes / 1024, color: css('--mean'), ratio: c.ratio,
      tip: `<b>${c.dataset}</b><br>columnstore ${(c.col_bytes / 1024).toFixed(0)} KiB, ${c.ratio}x smaller<br>same query results: ${c.identical ? 'yes' : 'NO'}`});
  }
  bars('compression', items, it => `${it.kib.toFixed(0)} KiB` + (it.ratio ? ` · ${it.ratio}x smaller` : ''), 100, 'Rowstore vs columnstore size');
}
function qtypes() {
  const names = {calibration: 'Calibration', main: 'Main question', follow_up: 'Follow-up'};
  const items = D.qtypes.filter(r => r.interview_type == null && r.kind).sort((a, b) => b.peak - a.peak)
    .map(r => ({label: names[r.kind] || r.kind, v: r.peak, color: css('--mean'),
      tip: `<b>${names[r.kind] || r.kind}</b><br>${r.questions} answers, ${r.high} went high (&gt; 70)<br>avg stress ${r.mean} · avg peak ${r.peak}`, r}));
  bars('qtypes', items, it => `${it.v.toFixed(0)} · ${it.r.high}/${it.r.questions} high`, 100, 'Average stress peak per question kind');
}

function render() { timeline(); latency(); compression(); qtypes(); }
qtable(); render();
let rt; addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(render, 120); });
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', render);
new MutationObserver(render).observe(document.documentElement, {attributes: true, attributeFilter: ['data-theme']});
"""


def body(d: dict) -> str:
    R, comp, lat = d["replay"], d["compression"], d["latency"]
    win = next(r for r in lat if r["query"].startswith("get_window_stats"))
    floor = next(r for r in lat if r["query"].startswith("network floor"))
    real = comp[0]
    big = comp[-1]
    n_main = sum(q["kind"] != "calibration" for q in R["questions"])
    peak = max(q["stats"]["stress_peak"] or 0 for q in R["questions"])
    by_kind = sorted((r for r in d["qtypes"] if r["interview_type"] is None and r["kind"]), key=lambda r: -r["peak"])
    n_answers = sum(r["questions"] for r in by_kind)
    n_types = len([r for r in d["qtypes"] if r["kind"] is None and r["interview_type"]])
    tiles = [
        ("Sessions on Tiger Data", f"{real['sessions']}", f"{real['rows']:,} vitals readings at 1 Hz"),
        ("get_window_stats on Tiger", f"{win['server']:.2f} ms", f"server time; {win['p50']:.0f} ms round trip (network floor {floor['p50']:.0f} ms)"),
        ("Columnstore", f"{big['ratio']}x smaller", f"{big['rows']:,} readings, segmentby session_id"),
        ("Replay below", f"peak {peak:.0f}", f"{n_main} questions, {len(R['series'])} points from vitals_5s"),
    ]
    tiles_html = "".join(f'<div class="tile"><div class="label">{a}</div><div class="value">{b}</div><div class="note">{c}</div></div>'
                         for a, b, c in tiles)
    return f"""<title>StressTest Vitals Dashboard</title>
<style>{CSS}</style>
<div class="wrap">
<header>
  <h1>StressTest vitals on Tiger Data</h1>
  <p>Webcam vitals stored once a second in a TimescaleDB hypertable, scored against each person's calm baseline,
  joined to interview questions by time range, and rolled into 5-second buckets for the replay. All numbers below are
  measured on our Tiger Data service. The sessions are mock sessions run through the real interview loop.</p>
</header>
<section class="tiles" aria-label="Summary">{tiles_html}</section>

<section class="panel" aria-labelledby="h-timeline">
  <h2 id="h-timeline">One session, replayed from the continuous aggregate</h2>
  <p class="sub">{R['session']['user_name']}, {n_main} questions. Stress is 0 to 100 against the calibration baseline
  (under 40 calm, 40 to 70 rising, over 70 high). Shaded bands are answer windows; + marks a follow-up. Hover for values.</p>
  <div class="legend"><span><i class="key" style="background:var(--mean)"></i>stress, mean per 5 s</span>
  <span><i class="key" style="background:var(--peak)"></i>stress, peak per 5 s</span><span>heart rate on its own axis below</span></div>
  <div class="chart" id="timeline"></div>
</section>

<section class="panel" aria-labelledby="h-q">
  <h2 id="h-q">How they reacted to each question</h2>
  <p class="sub">QuestionStats from the time-range join between each answer window and the vitals hypertable.</p>
  <div class="tablewrap"><table id="qtable"><thead><tr><th>#</th><th>Kind</th><th>Question</th><th class="n">Answer</th>
  <th class="n">HR mean</th><th class="n">HR vs base</th><th class="n">Stress mean</th><th class="n">Stress peak</th>
  <th>Level</th><th class="n">Peak at</th></tr></thead><tbody></tbody></table></div>
</section>

<div class="pair">
  <section class="panel" aria-labelledby="h-lat">
    <h2 id="h-lat">Query latency</h2>
    <p class="sub">20 runs each on a session with 10 minutes of data. Every query executes in under a millisecond on Tiger;
    the round trip is the network.</p>
    <div class="legend"><span><i class="dot" style="background:var(--mean)"></i>p50</span><span><i class="dot" style="background:var(--peak)"></i>p95</span></div>
    <div class="chart" id="latency"></div>
  </section>
  <section class="panel" aria-labelledby="h-comp">
    <h2 id="h-comp">Columnstore compression</h2>
    <p class="sub">vitals with segmentby session_id, orderby time DESC, measured on a copy so the live chunk is untouched.
    Query results are identical after conversion.</p>
    <div class="chart" id="compression"></div>
    <h2 id="h-qt" style="margin-top:10px">Which questions spike stress most</h2>
    <p class="sub">Average answer peak across {n_answers} answers in {n_types} interview types. Mock data: mock_vitals --auto
    spikes every other question, so this pattern is by construction until real sessions come in.</p>
    <div class="chart" id="qtypes"></div>
  </section>
</div>

<footer>Stored in Tiger Data (PostgreSQL 18.6, TimescaleDB 2.30.2): hypertable <span class="mono">vitals</span>,
continuous aggregate <span class="mono">vitals_5s</span>, columnstore. Built by <span class="mono">scripts/viz/build_dashboard.py</span>
from <span class="mono">frontend/src/mocks/replay_sample.json</span> and <span class="mono">docs/data/samples/</span>.</footer>
</div>
<script id="data" type="application/json">{json.dumps(d, separators=(",", ":")).replace("</", "<\\/")}</script>
<script>{JS}</script>
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fragment", type=Path, help="also write the page without the document wrapper")
    args = ap.parse_args()
    content = body(load_data())
    head, _, rest = content.partition('<div class="wrap">')
    doc = ("<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
           "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">\n"
           + head + "</head>\n<body>\n<div class=\"wrap\">" + rest + "</body>\n</html>\n")
    out = ROOT / "docs" / "data" / "dashboard.html"
    out.write_text(doc, encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT).as_posix()} ({len(doc) / 1024:.0f} KiB)")
    if args.fragment:
        args.fragment.write_text(content, encoding="utf-8")
        print(f"wrote fragment {args.fragment}")


if __name__ == "__main__":
    main()
