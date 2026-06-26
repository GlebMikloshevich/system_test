"""VisualizationModule — a styled, self-contained HTML report.

No external assets: all CSS is inlined so the file opens anywhere. The report
summarizes the run, the release outcome, per-document-type field metrics, any
errors, and a collapsible per-file breakdown. When a previous result is passed,
each match rate is annotated with a better / worse / unchanged marker.
"""

from __future__ import annotations

import html
from pathlib import Path

from ..integration.schemas import IngoreadStatus
from ..results.models import DocumentContainerPair, MeasurementsResult


def _esc(value: object) -> str:
    return html.escape(str(value))


def _pct(rate: float) -> str:
    return f"{rate * 100:.1f}%"


def _rate_class(rate: float) -> str:
    if rate >= 0.9:
        return "good"
    if rate >= 0.7:
        return "warn"
    return "bad"


def _bar(rate: float) -> str:
    cls = _rate_class(rate)
    width = max(0.0, min(1.0, rate)) * 100
    return (
        f"<div class='bar'><div class='bar-fill {cls}' style='width:{width:.1f}%'></div>"
        f"<span class='bar-label'>{_pct(rate)}</span></div>"
    )


def _delta_marker(curr: float, prev: float | None, tol: float) -> str:
    """Better / worse / unchanged vs the previous run (unchanged = within ±tol)."""
    if prev is None:
        return "<span class='delta new' title='not present in the previous run'>new</span>"
    pp = (curr - prev) * 100
    if abs(curr - prev) <= tol:
        return f"<span class='delta same' title='within ±{tol * 100:.0f}pp'>≈ {pp:+.1f}pp</span>"
    if curr > prev:
        return f"<span class='delta up' title='better than previous'>▲ {pp:+.1f}pp</span>"
    return f"<span class='delta down' title='worse than previous'>▼ {pp:+.1f}pp</span>"


def _badge(ok: bool, yes: str = "matched", no: str = "missed") -> str:
    return f"<span class='badge {'good' if ok else 'bad'}'>{yes if ok else no}</span>"


def _fmt_value(value: object) -> str:
    if isinstance(value, float):
        if value == float("inf"):
            return "∞"
        return f"{value:.3f}".rstrip("0").rstrip(".") if value % 1 else f"{value:.0f}"
    return _esc(value)


def _metric_chips(metrics: dict) -> str:
    if not metrics:
        return "<span class='muted'>—</span>"
    chips = "".join(
        f"<span class='chip'><b>{_esc(k)}</b>{_fmt_value(v)}</span>"
        for k, v in metrics.items()
    )
    return f"<div class='chips'>{chips}</div>"


def _status_badge(status: IngoreadStatus) -> str:
    cls = "good" if status == IngoreadStatus.COMPLETED else "bad"
    return f"<span class='badge {cls}'>{_esc(status.value)}</span>"


def _stat_card(label: str, value: str, hint: str = "") -> str:
    hint_html = f"<div class='card-hint'>{_esc(hint)}</div>" if hint else ""
    return (
        f"<div class='card'><div class='card-label'>{_esc(label)}</div>"
        f"<div class='card-value'>{value}</div>{hint_html}</div>"
    )


def _count_value(n: int) -> str:
    return f"<span class='bad'>{n}</span>" if n else str(n)


def _verdict_banner(result: MeasurementsResult) -> str:
    problems = []
    if result.failed:
        problems.append(f"{result.failed} failed")
    if result.timeouts:
        problems.append(f"{result.timeouts} timed out")
    if not result.document_results:
        problems.append("nothing was scored")
    if problems:
        return (
            "<div class='banner bad'><span class='dot'></span>"
            f"<b>Errors present</b> — {_esc(', '.join(problems))}. "
            "A pre-release gate would block this run.</div>"
        )
    return (
        "<div class='banner good'><span class='dot'></span>"
        "<b>No errors</b> — every document was processed and scored.</div>"
    )


def _errors_section(result: MeasurementsResult) -> str:
    bad = [
        cp
        for cp in result.container_pairs
        if cp.predictions.status != IngoreadStatus.COMPLETED or cp.predictions.error
    ]
    if not bad:
        return ""
    rows = "".join(
        f"<tr><td class='mono'>{_esc(cp.filename)}</td>"
        f"<td>{_status_badge(cp.predictions.status)}</td>"
        f"<td>{_esc(cp.predictions.error or '—')}</td>"
        f"<td class='num'>{cp.predictions.time:.2f}s</td></tr>"
        for cp in bad
    )
    return (
        f"<section><h2>Errors &amp; timeouts <span class='count'>{len(bad)}</span></h2>"
        "<table class='grid'><thead><tr><th>File</th><th>Status</th><th>Error</th>"
        f"<th>Time</th></tr></thead><tbody>{rows}</tbody></table></section>"
    )


def _document_sections(
    result: MeasurementsResult,
    prev_doc: dict[str, float],
    prev_field: dict[tuple[str, str], float],
    tol: float,
    show_delta: bool,
) -> str:
    if not result.document_results:
        return (
            "<section><div class='banner warn'><span class='dot'></span>"
            "No documents were scored — check for label mismatches between the "
            "scorer config and the predictions.</div></section>"
        )
    delta_th = "<th>vs prev</th>" if show_delta else ""
    out = []
    for doc in result.document_results:
        field_rows = ""
        for f in doc.field_results:
            delta_td = ""
            if show_delta:
                prev = prev_field.get((doc.label, f.field_name))
                delta_td = f"<td>{_delta_marker(f.match_rate, prev, tol)}</td>"
            field_rows += (
                f"<tr><td class='mono'>{_esc(f.field_name)}</td>"
                f"<td><span class='type'>{_esc(f.field_type.value)}</span></td>"
                f"<td class='bar-cell'>{_bar(f.match_rate)}</td>"
                f"<td>{_metric_chips(f.field_metrics)}</td>{delta_td}</tr>"
            )
        doc_marker = (
            _delta_marker(doc.match_rate, prev_doc.get(doc.label), tol) if show_delta else ""
        )
        out.append(
            "<section class='doc'>"
            f"<div class='doc-head'><h2>{_esc(doc.label)}</h2>"
            f"<div class='doc-meta'>{_bar(doc.match_rate)}"
            f"<span class='pill'>n = {doc.total_samples}</span>{doc_marker}</div></div>"
            "<table class='grid'><thead><tr><th>Field</th><th>Type</th>"
            f"<th>Match rate</th><th>Mean metrics</th>{delta_th}</tr></thead>"
            f"<tbody>{field_rows}</tbody></table></section>"
        )
    return "".join(out)


def _pair_label(pair) -> str:
    if pair.gt and pair.prediction:
        return _esc(pair.gt.doc_label)
    if pair.gt:
        return f"{_esc(pair.gt.doc_label)} <span class='muted'>(missed)</span>"
    if pair.prediction:
        return f"{_esc(pair.prediction.label)} <span class='muted'>(hallucinated)</span>"
    return "<span class='muted'>—</span>"


def _file_detail(cp: DocumentContainerPair) -> str:
    pair_rows = []
    for pair in cp.document_pairs:
        field_bits = "".join(
            f"<span class='chip {'good' if m.get('matched') else 'bad'}'>"
            f"{_esc(name)}</span>"
            for name, m in pair.field_metrics.items()
            if not name.startswith("__group__")
        )
        pair_rows.append(
            f"<tr><td>{_pair_label(pair)}</td><td>{_badge(pair.matched)}</td>"
            f"<td>{field_bits or '<span class=muted>—</span>'}</td></tr>"
        )
    n_match = sum(1 for p in cp.document_pairs if p.matched)
    return (
        "<details class='file'><summary>"
        f"<span class='mono'>{_esc(cp.filename)}</span> "
        f"{_status_badge(cp.predictions.status)} "
        f"<span class='muted'>{n_match}/{len(cp.document_pairs)} docs matched · "
        f"{cp.predictions.time:.2f}s</span></summary>"
        "<table class='grid inner'><thead><tr><th>Document</th><th>Result</th>"
        "<th>Fields</th></tr></thead><tbody>"
        f"{''.join(pair_rows) or '<tr><td colspan=3 class=muted>no documents</td></tr>'}"
        "</tbody></table></details>"
    )


def _files_section(result: MeasurementsResult) -> str:
    if not result.container_pairs:
        return ""
    details = "".join(_file_detail(cp) for cp in result.container_pairs)
    return (
        f"<section><h2>Per-file breakdown <span class='count'>"
        f"{len(result.container_pairs)}</span></h2>"
        "<p class='muted'>Green chips = field matched, red = missed. Click a file "
        "to expand.</p>"
        f"{details}</section>"
    )


def render_html(
    result: MeasurementsResult,
    output_dir: Path,
    previous: MeasurementsResult | None = None,
    tolerance: float = 0.0,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = result.start_date.strftime("%Y%m%dT%H%M%S")
    out = output_dir / f"{stamp}__{result.test_config_name}.html"

    show_delta = previous is not None
    prev_doc = {d.label: d.match_rate for d in previous.document_results} if previous else {}
    prev_field = (
        {(d.label, f.field_name): f.match_rate
         for d in previous.document_results for f in d.field_results}
        if previous
        else {}
    )

    overall_value = (
        f"<span class='big {_rate_class(result.match_rate)}'>{_pct(result.match_rate)}</span>"
    )
    if previous is not None:
        overall_value += f" {_delta_marker(result.match_rate, previous.match_rate, tolerance)}"

    docs_scored = sum(d.total_samples for d in result.document_results)
    cards = "".join(
        [
            _stat_card("Overall match rate", overall_value, "documents fully correct"),
            _stat_card("Document types", str(len(result.document_results))),
            _stat_card("Documents scored", str(docs_scored)),
            _stat_card("Files", str(len(result.container_pairs))),
            _stat_card("Failed", _count_value(result.failed)),
            _stat_card("Timeouts", _count_value(result.timeouts)),
            _stat_card("Total time", f"{result.total_time:.2f}s"),
            _stat_card("Time / sample", f"{result.time_per_sample:.3f}s"),
        ]
    )

    meta_items = [
        f"scorer: {_esc(result.scorer_config_name)}",
        _esc(result.start_date.strftime("%Y-%m-%d %H:%M UTC")),
    ]
    if show_delta:
        meta_items.append(f"compared to previous (unchanged ≤ ±{tolerance * 100:.0f}pp)")
    meta = "<span class='sep'>·</span>".join(f"<span>{m}</span>" for m in meta_items)

    html_doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(result.test_config_name)} · ingoread-test report</title>
<style>{_CSS}</style></head>
<body>
<div class="wrap">
  <header>
    <div class="eyebrow">ingoread-test report</div>
    <h1>{_esc(result.test_config_name)}</h1>
    <div class="sub">{meta}</div>
  </header>
  {_verdict_banner(result)}
  <section class="cards">{cards}</section>
  {_document_sections(result, prev_doc, prev_field, tolerance, show_delta)}
  {_errors_section(result)}
  {_files_section(result)}
  <footer>Generated by ingoread-test · match rate = fraction of documents where
    every scored field matched.</footer>
</div>
</body></html>
"""
    out.write_text(html_doc, encoding="utf-8")
    return out


_CSS = """
:root{
  --panel:#ffffff; --ink:#1f2937; --muted:#6b7280; --line:#e5e7eb;
  --good:#10b981; --warn:#f59e0b; --bad:#ef4444; --accent:#6366f1; --soft:#f9fafb;
}
*{box-sizing:border-box}
body{margin:0;background:#eef2f7;color:var(--ink);
  font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;}
.wrap{max-width:1040px;margin:0 auto;padding:28px 20px 60px}
header{background:linear-gradient(135deg,#4f46e5,#7c3aed);color:#fff;
  padding:26px 28px;border-radius:16px;box-shadow:0 10px 30px rgba(79,70,229,.25)}
.eyebrow{text-transform:uppercase;letter-spacing:.12em;font-size:11px;opacity:.85}
header h1{margin:6px 0 6px;font-size:28px;font-weight:700}
header .sub{display:flex;flex-wrap:wrap;align-items:center;gap:8px;
  font-size:13px;opacity:.9}
header .sub .sep{opacity:.5}
.banner{display:flex;align-items:center;gap:10px;margin:18px 0;padding:14px 18px;
  border-radius:12px;font-size:14px;border:1px solid}
.banner.good{background:#ecfdf5;border-color:#a7f3d0;color:#065f46}
.banner.bad{background:#fef2f2;border-color:#fecaca;color:#991b1b}
.banner.warn{background:#fffbeb;border-color:#fde68a;color:#92400e}
.banner .dot{width:9px;height:9px;border-radius:50%;background:currentColor;flex:0 0 auto}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
  gap:14px;margin:18px 0 8px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;
  padding:14px 16px;box-shadow:0 1px 2px rgba(0,0,0,.03)}
.card-label{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.card-value{font-size:24px;font-weight:700;margin-top:4px}
.card-hint{font-size:11px;color:var(--muted);margin-top:2px}
.big{font-size:30px;font-weight:800}
.good{color:var(--good)} .warn{color:var(--warn)} .bad{color:var(--bad)}
.delta{font-size:12px;font-weight:600;white-space:nowrap;vertical-align:middle}
.delta.up{color:var(--good)} .delta.down{color:var(--bad)}
.delta.same{color:var(--muted)} .delta.new{color:var(--accent)}
section{margin:26px 0}
h2{font-size:18px;margin:0 0 12px;display:flex;align-items:center;gap:10px}
.count{background:var(--accent);color:#fff;font-size:12px;border-radius:999px;
  padding:1px 9px;font-weight:600}
.doc{background:var(--panel);border:1px solid var(--line);border-radius:14px;
  padding:18px 20px;box-shadow:0 1px 2px rgba(0,0,0,.03)}
.doc-head{display:flex;justify-content:space-between;align-items:center;
  gap:16px;flex-wrap:wrap;margin-bottom:10px}
.doc-head h2{margin:0}
.doc-meta{display:flex;align-items:center;gap:12px;min-width:240px}
table.grid{width:100%;border-collapse:collapse;font-size:14px}
table.grid th{text-align:left;color:var(--muted);font-weight:600;font-size:12px;
  text-transform:uppercase;letter-spacing:.03em;padding:8px 10px;
  border-bottom:2px solid var(--line)}
table.grid td{padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:middle}
table.grid tbody tr:hover{background:var(--soft)}
table.inner{margin-top:10px}
.bar-cell{width:220px}
.bar{position:relative;background:#eef2f7;border-radius:8px;height:20px;width:200px;overflow:hidden}
.bar-fill{position:absolute;left:0;top:0;bottom:0;border-radius:8px}
.bar-fill.good{background:var(--good)} .bar-fill.warn{background:var(--warn)}
.bar-fill.bad{background:var(--bad)}
.bar-label{position:relative;z-index:1;font-size:12px;font-weight:700;
  line-height:20px;padding-left:8px;color:#11261f;mix-blend-mode:luminosity}
.badge{display:inline-block;padding:2px 9px;border-radius:999px;font-size:12px;font-weight:600}
.badge.good{background:#ecfdf5;color:#047857} .badge.bad{background:#fef2f2;color:#b91c1c}
.pill{background:#eef2ff;color:#4338ca;border-radius:999px;padding:2px 10px;
  font-size:12px;font-weight:600}
.type{background:#f1f5f9;color:#334155;border-radius:6px;padding:2px 8px;font-size:12px;
  font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{background:#f1f5f9;border-radius:6px;padding:2px 8px;font-size:12px;
  font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:#334155}
.chip b{color:#0f172a;margin-right:5px;font-weight:700}
.chip.good{background:#ecfdf5;color:#047857} .chip.bad{background:#fef2f2;color:#b91c1c}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px}
.num,.grid td.num{text-align:right;font-variant-numeric:tabular-nums}
.muted{color:var(--muted)}
details.file{background:var(--panel);border:1px solid var(--line);border-radius:12px;
  padding:4px 14px;margin:8px 0}
details.file summary{cursor:pointer;padding:10px 4px;display:flex;align-items:center;
  gap:10px;flex-wrap:wrap}
details.file[open]{box-shadow:0 1px 2px rgba(0,0,0,.04)}
footer{margin-top:40px;color:var(--muted);font-size:12px;text-align:center}
"""
