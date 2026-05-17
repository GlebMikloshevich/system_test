"""VisualizationModule — minimal HTML report next to the result JSON."""

from __future__ import annotations

import html
from pathlib import Path

from ..results.models import MeasurementsResult


def render_html(result: MeasurementsResult, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = result.start_date.strftime("%Y%m%dT%H%M%S")
    out = output_dir / f"{stamp}__{result.test_config_name}.html"

    rows = []
    for doc in result.document_results:
        field_rows = "".join(
            f"<tr><td>{html.escape(f.field_name)}</td>"
            f"<td>{f.field_type.value}</td>"
            f"<td>{f.match_rate:.3f}</td>"
            f"<td>{html.escape(str(f.field_metrics))}</td></tr>"
            for f in doc.field_results
        )
        rows.append(
            f"<h2>{html.escape(doc.label)} "
            f"(match_rate={doc.match_rate:.3f}, n={doc.total_samples})</h2>"
            "<table border='1' cellpadding='4'>"
            "<tr><th>Field</th><th>Type</th><th>Match rate</th><th>Metrics</th></tr>"
            f"{field_rows}</table>"
        )

    body = "".join(rows)
    html_doc = f"""<!doctype html>
<html><head><meta charset='utf-8'><title>{html.escape(result.test_config_name)}</title></head>
<body>
<h1>{html.escape(result.test_config_name)} / {html.escape(result.scorer_config_name)}</h1>
<p>
  start={result.start_date.isoformat()}<br>
  match_rate={result.match_rate:.3f}<br>
  total_samples={result.total_samples}, timeouts={result.timeouts}, failed={result.failed}<br>
  total_time={result.total_time:.2f}s, time_per_sample={result.time_per_sample:.2f}s
</p>
{body}
</body></html>
"""
    out.write_text(html_doc, encoding="utf-8")
    return out
