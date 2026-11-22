"""Self-contained HTML evidence reports (inline SVG, no network, no JS libraries).

A report is written into the evidence directory *before* it is signed, so it is
covered by the manifest like every other file. It only displays what Referee
measured; the verdict comes from verdict.json, never from the page.
"""
from __future__ import annotations

import csv
import html
import json
import re
import math
from pathlib import Path

E = html.escape

CSS = """
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
--grid:#e1e0d9;--axis:#c3c2b7;--s1:#2a78d6;--s2:#eb6834;--good:#0ca30c;--warn:#fab219;--crit:#d03b3b;
--excl:#c3c2b7;--codebg:#f0efec;--add:#e3f3e3;--del:#fbe4e4}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){color-scheme:dark;--page:#0d0d0d;
--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--grid:#2c2c2a;--axis:#383835;--s1:#3987e5;--s2:#d95926;
--excl:#4a4a46;--codebg:#232321;--add:#16301a;--del:#3a1d1d}}
:root[data-theme="dark"]{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;
--grid:#2c2c2a;--axis:#383835;--s1:#3987e5;--s2:#d95926;--excl:#4a4a46;--codebg:#232321;--add:#16301a;--del:#3a1d1d}
*{box-sizing:border-box}body{margin:0;background:var(--page);color:var(--ink);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1040px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:32px 0 8px}h3{font-size:15px;margin:16px 0 6px}
.sub{color:var(--ink2);margin:0 0 16px}.card{background:var(--surface);border:1px solid var(--grid);
border-radius:10px;padding:16px;margin:12px 0}
.badge{display:inline-flex;align-items:center;gap:6px;font-weight:700;padding:4px 10px;border-radius:999px;
font-size:14px;color:#fff}.PASS{background:var(--good)}.FAIL,.TAMPER{background:var(--crit)}
.INCONCLUSIVE{background:var(--warn);color:#0b0b0b}
table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:6px 8px;
border-bottom:1px solid var(--grid);vertical-align:top}th{color:var(--ink2);font-weight:600}
td.num{font-variant-numeric:tabular-nums}table.checks td:nth-child(1),table.checks td:nth-child(2),table.checks td:nth-child(4){white-space:nowrap}.ok{color:var(--good);font-weight:700}.bad{color:var(--crit);font-weight:700}
.kv{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:8px 16px}
.kv div span{display:block;color:var(--muted);font-size:12px}
svg{display:block;width:100%;height:auto}svg text{fill:var(--muted);font-size:11px}
.legend{display:flex;gap:16px;flex-wrap:wrap;font-size:13px;color:var(--ink2);margin:4px 0}
.sw{display:inline-block;width:12px;height:12px;border-radius:3px;vertical-align:-1px;margin-right:6px}
pre{background:var(--codebg);padding:10px;border-radius:8px;overflow:auto;font-size:12px;line-height:1.45;max-height:420px}
code{font-family:ui-monospace,Consolas,monospace}.add{background:var(--add)}.del{background:var(--del)}
details summary{cursor:pointer;color:var(--ink2)}.round{border-left:4px solid var(--grid)}
.round.PASSb{border-left-color:var(--good)}.round.FAILb,.round.TAMPERb{border-left-color:var(--crit)}
.round.INCONCLUSIVEb{border-left-color:var(--warn)}.tip{position:fixed;pointer-events:none;background:var(--surface);
border:1px solid var(--axis);border-radius:6px;padding:4px 8px;font-size:12px;display:none;color:var(--ink)}
a{color:var(--s1)}.note{color:var(--ink2);font-size:13px}
"""

TIP_JS = """
const tip=document.createElement('div');tip.className='tip';document.body.appendChild(tip);
document.querySelectorAll('[data-tip]').forEach(el=>{el.addEventListener('mousemove',e=>{tip.textContent=el.dataset.tip;
tip.style.display='block';tip.style.left=(e.clientX+12)+'px';tip.style.top=(e.clientY+12)+'px'});
el.addEventListener('mouseleave',()=>tip.style.display='none')});
document.querySelectorAll('svg[data-series]').forEach(svg=>{const d=JSON.parse(svg.dataset.series);
const vb=svg.viewBox.baseVal;const line=svg.querySelector('.xhair');
svg.addEventListener('mousemove',e=>{const r=svg.getBoundingClientRect();const x=(e.clientX-r.left)*vb.width/r.width;
let best=null,bd=1e9;for(const p of d){const dd=Math.abs(p[0]-x);if(dd<bd){bd=dd;best=p}}
if(!best||bd>20){tip.style.display='none';line.setAttribute('opacity',0);return}
line.setAttribute('x1',best[0]);line.setAttribute('x2',best[0]);line.setAttribute('opacity',1);
tip.textContent=best[1];tip.style.display='block';tip.style.left=(e.clientX+12)+'px';tip.style.top=(e.clientY+12)+'px'});
svg.addEventListener('mouseleave',()=>{tip.style.display='none';line.setAttribute('opacity',0)})});
"""


_US = re.compile(r"(?<=\d) us\b")


def _page(title: str, body: str) -> str:
    body = _US.sub(" µs", body).replace("&lt;= ", "≤ ").replace("&gt;= ", "≥ ")
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'><title>{E(title)}</title>"
            f"<style>{CSS}</style></head><body><main>{body}</main><script>{TIP_JS}</script></body></html>")


def _badge(v: str) -> str:
    icon = {"PASS": "&#10003;", "FAIL": "&#10007;", "TAMPER": "&#9888;", "INCONCLUSIVE": "?"}.get(v, "")
    return f"<span class='badge {E(v)}'>{icon} {E(v)}</span>"


# ------------------------------------------------------------------ charts

W, H, L, R, T, B = 960, 260, 56, 16, 14, 34   # svg size and plot margins


def _logy(v, lo, hi):
    v = min(max(v, lo), hi)
    return T + (H - T - B) * (1 - (math.log10(v) - math.log10(lo)) / (math.log10(hi) - math.log10(lo)))


def _hline(y, label, cls_color):
    return (f"<line x1='{L}' x2='{W - R}' y1='{y:.1f}' y2='{y:.1f}' stroke='{cls_color}' stroke-width='1.5' "
            f"stroke-dasharray='5 4'/><text x='{W - R - 4}' y='{y - 5:.1f}' text-anchor='end' "
            f"style='fill:{cls_color}'>{E(label)}</text>")


def histogram(lat: list[float], deadline: float, p99: float) -> str:
    lo, hi = 0.5, max(10_000.0, deadline * 10)
    edges = [lo * (10 ** (i / 10)) for i in range(0, int(10 * math.log10(hi / lo)) + 1)]
    counts = [0] * (len(edges) - 1)
    for v in lat:
        j = int(10 * math.log10(max(v, lo) / lo))
        counts[min(j, len(counts) - 1)] += 1
    xs = lambda v: L + (W - L - R) * (math.log10(max(v, lo)) - math.log10(lo)) / (math.log10(hi) - math.log10(lo))
    cmax = max(counts) or 1
    ys = lambda c: T + (H - T - B) * (1 - math.log10(c + 1) / math.log10(cmax + 1))
    out = [f"<svg viewBox='0 0 {W} {H}' role='img' aria-label='Reply latency histogram'>"]
    for c in (1, 10, 100, 1000, 10000):
        if c <= cmax:
            out.append(f"<line x1='{L}' x2='{W - R}' y1='{ys(c):.1f}' y2='{ys(c):.1f}' stroke='var(--grid)'/>"
                       f"<text x='{L - 6}' y='{ys(c) + 4:.1f}' text-anchor='end'>{c}</text>")
    for i, c in enumerate(counts):
        if not c:
            continue
        x0, x1 = xs(edges[i]) + 1, xs(edges[i + 1]) - 1
        y = ys(c)
        out.append(f"<rect x='{x0:.1f}' y='{y:.1f}' width='{max(1, x1 - x0):.1f}' height='{H - B - y:.1f}' rx='2' "
                   f"fill='var(--s1)' data-tip='{edges[i]:.1f}-{edges[i + 1]:.1f} us: {c} replies'/>")
    for v in (1, 10, 100, 1000, 10000):
        if v <= hi:
            out.append(f"<text x='{xs(v):.1f}' y='{H - B + 16}' text-anchor='{'end' if xs(v) > W - R - 30 else 'middle'}'>{v} us</text>")
    out.append(f"<line x1='{L}' x2='{W - R}' y1='{H - B}' y2='{H - B}' stroke='var(--axis)'/>")
    for v, label, col in ((deadline, f"deadline {deadline:g} us", "var(--crit)"), (p99, f"p99 {p99:.1f} us", "var(--ink2)")):
        x = xs(v)
        out.append(f"<line x1='{x:.1f}' x2='{x:.1f}' y1='{T}' y2='{H - B}' stroke='{col}' stroke-width='1.5' "
                   f"stroke-dasharray='5 4'/><text x='{x + 4:.1f}' y='{T + 10}' style='fill:{col}'>{E(label)}</text>")
    out.append(f"<text x='{L}' y='{H - 4}'>replies per bin (log scale) by reply latency (log scale)</text></svg>")
    return "".join(out)


def latency_over_time(rows: list[dict], cps: float, deadline: float, stalled: set, held: set, warm: int) -> str:
    n = len(rows)
    if not n:
        return ""
    lo, hi = 0.5, max(10_000.0, deadline * 10)
    t = lambda i: L + (W - L - R) * i / max(1, n - 1)
    bucket = max(1, n // 400)
    pts, series = [], []
    for b0 in range(0, n, bucket):
        chunk = [int(r["latency_cyc"]) * 1e6 / cps for r in rows[b0:b0 + bucket] if int(r["status"]) == 0]
        if chunk:
            m = max(chunk)
            x, y = t(b0), _logy(m, lo, hi)
            pts.append(f"{x:.1f},{y:.1f}")
            series.append([round(x, 1), f"t={b0 / 1000:.2f} s  worst reply {m:.1f} us"])
    out = [f"<svg viewBox='0 0 {W} {H}' role='img' aria-label='Reply latency over time' "
           f"data-series='{E(json.dumps(series))}'>"]
    for v in (1, 10, 100, 1000, 10000):
        if v <= hi:
            out.append(f"<line x1='{L}' x2='{W - R}' y1='{_logy(v, lo, hi):.1f}' y2='{_logy(v, lo, hi):.1f}' "
                       f"stroke='var(--grid)'/><text x='{L - 6}' y='{_logy(v, lo, hi) + 4:.1f}' text-anchor='end'>{v} us</text>")
    if warm:
        out.append(f"<rect x='{L}' y='{T}' width='{t(warm) - L:.1f}' height='{H - T - B}' fill='var(--excl)' opacity='.35' "
                   f"data-tip='warm-up: not judged'/>")
    seq_idx = {int(r["seq"]): i for i, r in enumerate(rows)}
    for s in sorted(stalled):
        if s in seq_idx:
            x = t(seq_idx[s])
            out.append(f"<rect x='{x - 1.5:.1f}' y='{T}' width='3' height='{H - T - B}' fill='var(--excl)' "
                       f"data-tip='seq {s}: target stall, excluded'/>")
    for s in sorted(held):
        if s in seq_idx:
            x = t(seq_idx[s])
            out.append(f"<rect x='{x - 1.5:.1f}' y='{T}' width='3' height='{H - T - B}' fill='var(--crit)' "
                       f"data-tip='seq {s}: plant held up by the controller'/>")
    out.append(f"<polyline points='{' '.join(pts)}' fill='none' stroke='var(--s1)' stroke-width='2' "
               f"stroke-linejoin='round'/>")
    for i, r in enumerate(rows):
        v = int(r["latency_cyc"]) * 1e6 / cps
        if int(r["status"]) == 0 and v > deadline and int(r["period"]) >= warm and int(r["seq"]) not in stalled:
            out.append(f"<circle cx='{t(i):.1f}' cy='{_logy(v, lo, hi):.1f}' r='4' fill='var(--crit)' "
                       f"stroke='var(--surface)' stroke-width='2' data-tip='seq {r['seq']}: {v:.1f} us, late'/>")
    out.append(_hline(_logy(deadline, lo, hi), f"deadline {deadline:g} us", "var(--crit)"))
    out.append(f"<line class='xhair' y1='{T}' y2='{H - B}' stroke='var(--axis)' opacity='0'/>")
    secs = n / 1000
    for k in range(0, int(secs) + 1, max(1, int(secs) // 10)):
        out.append(f"<text x='{t(k * 1000):.1f}' y='{H - B + 16}' text-anchor='middle'>{k} s</text>")
    out.append(f"<line x1='{L}' x2='{W - R}' y1='{H - B}' y2='{H - B}' stroke='var(--axis)'/></svg>")
    return "".join(out)


def tracking(rows: list[dict]) -> str:
    n = len(rows)
    if not n:
        return ""
    step = max(1, n // 800)
    sel = rows[::step]
    ys = [float(r["y"]) for r in sel] + [float(r["setpoint"]) for r in sel]
    lo, hi = min(ys) - 0.1, max(ys) + 0.1
    t = lambda i: L + (W - L - R) * i / max(1, n - 1)
    yy = lambda v: T + (H - T - B) * (1 - (v - lo) / (hi - lo))
    sp = " ".join(f"{t(i * step):.1f},{yy(float(r['setpoint'])):.1f}" for i, r in enumerate(sel))
    pv = " ".join(f"{t(i * step):.1f},{yy(float(r['y'])):.1f}" for i, r in enumerate(sel))
    series = [[round(t(i * step), 1), f"t={i * step / 1000:.2f} s  y={float(r['y']):.3f}  setpoint={float(r['setpoint']):.2f}"]
              for i, r in enumerate(sel)]
    out = [f"<svg viewBox='0 0 {W} {H}' role='img' aria-label='Plant output versus setpoint' "
           f"data-series='{E(json.dumps(series))}'>"]
    for v in (-0.5, 0, 0.5, 1.0):
        if lo <= v <= hi:
            out.append(f"<line x1='{L}' x2='{W - R}' y1='{yy(v):.1f}' y2='{yy(v):.1f}' stroke='var(--grid)'/>"
                       f"<text x='{L - 6}' y='{yy(v) + 4:.1f}' text-anchor='end'>{v:g}</text>")
    out.append(f"<polyline points='{sp}' fill='none' stroke='var(--s2)' stroke-width='2' stroke-dasharray='6 4'/>")
    out.append(f"<polyline points='{pv}' fill='none' stroke='var(--s1)' stroke-width='2' stroke-linejoin='round'/>")
    out.append(f"<line class='xhair' y1='{T}' y2='{H - B}' stroke='var(--axis)' opacity='0'/>")
    secs = n / 1000
    for k in range(0, int(secs) + 1, max(1, int(secs) // 10)):
        out.append(f"<text x='{t(k * 1000):.1f}' y='{H - B + 16}' text-anchor='middle'>{k} s</text>")
    out.append(f"<line x1='{L}' x2='{W - R}' y1='{H - B}' y2='{H - B}' stroke='var(--axis)'/></svg>")
    return "".join(out)


# ------------------------------------------------------------------ pages

def _manifest_table(ev: Path) -> str:
    man = ev / "manifest.json"
    if not man.exists():
        return "<p class='note'>Files are hashed and signed into manifest.json when the run is sealed.</p>"
    files = json.loads(man.read_text()).get("files", {})
    rows = "".join(f"<tr><td><code>{E(k)}</code></td><td><code>{E(str(v)[:16])}...</code></td></tr>"
                   for k, v in sorted(files.items()))
    return f"<details><summary>{len(files)} files in the signed manifest</summary><table>{rows}</table></details>"


def check_report(ev: Path) -> str:
    v = json.loads((ev / "verdict.json").read_text())
    spec = json.loads((ev / "spec.json").read_text()) if (ev / "spec.json").exists() else {}
    rt = spec.get("realtime_loop", {})
    s, plant, ld, run = v.get("stats") or {}, v.get("plant") or {}, v.get("load") or {}, v.get("run") or {}
    deadline = float(rt.get("deadline_us", 200))
    body = [f"<h1>Referee verdict {_badge(v['verdict'])}</h1>",
            f"<p class='sub'>run {E(v['run_id'])} &middot; {E(Path(v.get('project', '')).name)} &middot; "
            f"spec: {E(v.get('spec_source', ''))}</p>"]
    rows = "".join(f"<tr><td class='{'ok' if c['pass'] else 'bad'}'>{'&#10003; ok' if c['pass'] else '&#10007; FAIL'}</td>"
                   f"<td>{E(c['name'])}</td><td class='num'>{E(str(c['measured']))}</td><td>{E(str(c['limit']))}</td></tr>"
                   for c in v.get("checks", []))
    body.append(f"<div class='card'><table class='checks'><tr><th></th><th>check</th><th>measured by Referee</th><th>limit</th></tr>"
                f"{rows}</table></div>")
    kv = [("Duration", f"{v.get('duration_s')} s at {rt.get('rate_hz', '?')} Hz"),
          ("Background load", f"{ld.get('threads')} threads @ prio {ld.get('priority')}, duty {ld.get('duty_actual', 0):.2f}"
           if v.get("with_load") and ld else "none"),
          ("Plant / monitor priority", f"{plant.get('priority', '?')} / {plant.get('monitor_priority') or 'off'}"),
          ("Stall attribution", s.get("stall_attribution", "-")),
          ("Controller user", run.get("controller_user", "-")),
          ("Build sandbox", "on" if v.get("build_sandbox") else "off"),
          ("Artifact sha256", (v.get("artifact_sha256") or "-")[:16] + "..."),
          ("Elapsed", f"{v.get('elapsed_s')} s")]
    body.append("<div class='card kv'>" + "".join(f"<div><span>{E(k)}</span>{E(str(val))}</div>" for k, val in kv) + "</div>")

    if (ev / "samples.csv").exists() and plant.get("cycles_per_sec"):
        cps = float(plant["cycles_per_sec"])
        rows_all = list(csv.DictReader((ev / "samples.csv").open()))
        warm = int(float(spec.get("run", {}).get("warmup_s", 0.5)) * float(rt.get("rate_hz", 1000)))
        stalled, held = set(s.get("stalled_seqs", [])), set(s.get("preempted_seqs", []))
        lat = [int(r["latency_cyc"]) * 1e6 / cps for r in rows_all
               if int(r["status"]) == 0 and int(r["period"]) >= warm and int(r["seq"]) not in stalled]
        l = s.get("latency_us", {})
        body.append("<h2>Reply latency</h2><p class='sub'>Time from the plant's MsgSend to the controller's reply, "
                    "timed on the target with ClockCycles. Judged samples only.</p>")
        body.append(f"<div class='card'>{histogram(lat, deadline, l.get('p99', 0))}</div>")
        body.append("<h2>Latency over time</h2><p class='sub'>Worst reply in each bucket. Red dots: late replies. "
                    "Grey: target stalls confirmed by Referee's monitor (excluded and counted). Red bands: the "
                    "controller held Referee's plant up.</p>")
        body.append(f"<div class='legend'><span><i class='sw' style='background:var(--s1)'></i>worst reply per bucket</span>"
                    f"<span><i class='sw' style='background:var(--crit)'></i>late reply / plant held up</span>"
                    f"<span><i class='sw' style='background:var(--excl)'></i>excluded (warm-up, stall)</span></div>")
        body.append(f"<div class='card'>{latency_over_time(rows_all, cps, deadline, stalled, held, warm)}</div>")
        body.append("<h2>Control quality</h2><p class='sub'>Plant output y against the setpoint. Every reply was also "
                    f"checked against Referee's own PID: {E(str(s.get('model_bad')))} off, max error "
                    f"{s.get('model_max_abs_err', 0):.3g}.</p>")
        body.append("<div class='legend'><span><i class='sw' style='background:var(--s1)'></i>plant output y</span>"
                    "<span><i class='sw' style='background:var(--s2)'></i>setpoint</span></div>")
        body.append(f"<div class='card'>{tracking(rows_all)}</div>")
        stat_rows = [("p50", l.get("p50")), ("p99", l.get("p99")), ("p99.9", l.get("p999")), ("max", l.get("max"))]
        body.append("<details><summary>Numbers as a table</summary><table><tr><th>statistic</th><th>value</th></tr>"
                    + "".join(f"<tr><td>latency {k}</td><td class='num'>{val:.1f} us</td></tr>" for k, val in stat_rows if val is not None)
                    + "".join(f"<tr><td>{E(k)}</td><td class='num'>{E(str(s.get(k)))}</td></tr>"
                              for k in ("judged_periods", "replies", "late", "skipped", "errors", "missed", "stalled_samples",
                                        "held_by_other", "preempted", "ctl_samples", "ctl_violations", "tracking_rms"))
                    + "</table></details>")
    body.append("<h2>Evidence</h2>" + _manifest_table(ev) +
                "<p class='note'>Check it yourself: <code>referee verify &lt;this folder&gt;</code> re-hashes every file "
                "and checks the ed25519 signature. The page shows Referee's numbers; verdict.json is the record.</p>")
    return _page(f"Referee {v['verdict']} {v['run_id']}", "".join(body))


def _diff_html(diff: str, limit: int = 400) -> str:
    out = []
    for line in diff.splitlines()[:limit]:
        cls = "add" if line.startswith("+") and not line.startswith("+++") else \
              "del" if line.startswith("-") and not line.startswith("---") else ""
        out.append(f"<span class='{cls}'>{E(line)}</span>")
    more = len(diff.splitlines()) - limit
    return "<pre><code>" + "\n".join(out) + (f"\n... {more} more lines" if more > 0 else "") + "</code></pre>"


def pack_report(pack: Path) -> str:
    tl = json.loads((pack / "timeline.json").read_text())
    body = [f"<h1>Agent run {_badge(tl['final_verdict'])}</h1>",
            f"<p class='sub'>{E(tl['run_id'])} &middot; agent backend: {E(tl['backend'])} &middot; "
            f"{len(tl['rounds'])} round{'' if len(tl['rounds']) == 1 else 's'} &middot; protected: {E(', '.join(tl.get('protected', [])))}</p>"]
    if tl["backend"] == "replay":
        body.append("<div class='card'><b>Scripted agent.</b> These rounds were replayed from files, not written by a "
                    "live model. Used for regression tests and the labelled cheating demo.</div>")
    for r in tl["rounds"]:
        rd = pack / f"round-{r['round']}"
        rec = json.loads((rd / "round.json").read_text()) if (rd / "round.json").exists() else {}
        v = r["verdict"]
        stats = [f"missed {r['missed']}" if r.get("missed") is not None else "",
                 f"p99 {r['p99_us']:.1f} us" if r.get("p99_us") is not None and r["p99_us"] == r["p99_us"] else "",
                 f"{r['warnings']} warning{'' if r['warnings'] == 1 else 's'}" if r.get("warnings") else ""]
        card = [f"<div class='card round {E(v)}b'><h3>Round {r['round']} {_badge(v)} "
                f"<span class='note'>{E(' / '.join(x for x in stats if x))}</span></h3>",
                f"<p><b>Agent said:</b> {E(r.get('agent_summary') or '')}</p>"]
        for e in rec.get("tamper", []):
            card.append(f"<p class='bad'>&#9888; TAMPER: {E(e['change'])} {E(e['path'])} (reverted)</p>")
            if e.get("diff"):
                card.append(_diff_html(e["diff"]))
        if rec.get("warnings"):
            card.append("<p><b>Flagged for the reviewer:</b></p><table>" + "".join(
                f"<tr><td>{E(w['rule'])}</td><td><code>{E(w['file'])}</code></td><td><code>{E(w['line'])}</code></td></tr>"
                for w in rec["warnings"]) + "</table>")
        for c in (rec.get("check") or {}).get("checks", []):
            if not c["pass"]:
                card.append(f"<p class='bad'>&#10007; {E(c['name'])}: {E(str(c['measured']))} (limit {E(str(c['limit']))})</p>")
        if (rd / "check" / "report.html").exists():
            card.append(f"<p><a href='round-{r['round']}/check/report.html'>Full measurement report for this round</a></p>")
        if (rd / "changes.diff").exists() and (rd / "changes.diff").read_text().strip():
            card.append("<details><summary>What the agent changed (diff)</summary>"
                        + _diff_html((rd / "changes.diff").read_text()) + "</details>")
        card.append("</div>")
        body.append("".join(card))
    body.append("<h2>Evidence</h2>" + _manifest_table(pack) +
                "<p class='note'>Each round was signed the moment it finished, before the agent's next turn. "
                "<code>referee verify &lt;this folder&gt;</code> checks the pack and every round inside it.</p>")
    return _page(f"Referee agent run {tl['run_id']}", "".join(body))


def write_report(d: Path, out: Path | None = None) -> Path:
    if (d / "timeline.json").exists():
        page = pack_report(d)
    elif (d / "verdict.json").exists():
        page = check_report(d)
    else:
        raise FileNotFoundError(f"{d}: no verdict.json or timeline.json")
    out = out or d / "report.html"
    out.write_text(page, encoding="utf-8")
    return out
