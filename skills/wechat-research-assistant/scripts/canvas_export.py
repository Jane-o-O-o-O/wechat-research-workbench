"""Export private evidence chains to a self-contained local canvas."""
from datetime import datetime
import json
from pathlib import Path
import uuid

from research_sources import SearchError
from workflow import TZ, atomic_write, read_json


def export_canvas(private, selected=None):
    chains = private / 'search' / 'chains'
    paths = [selected] if selected else sorted(chains.glob('*.json'))
    reports = []
    for path in paths:
        if not path.is_file() or not path.resolve().is_relative_to(chains.resolve()):
            raise SearchError('canvas_requires_private_chain_report')
        report = read_json(path)
        if (not isinstance(report, dict) or not isinstance(report.get('subject'), str)
                or not isinstance(report.get('claims'), list)):
            raise SearchError('invalid_canvas_chain_report')
        reports.append(report)
    if not reports:
        raise SearchError('canvas_has_no_chain_reports')
    reports.sort(key=lambda report: str(report.get('reviewed_at', '')), reverse=True)
    template = Path(__file__).resolve().parents[1] / 'assets' / 'investigation-canvas.html'
    marker = '<script type="application/json" id="embedded-reports">null</script>'
    html = template.read_text(encoding='utf-8')
    if html.count(marker) != 1:
        raise SearchError('canvas_template_invalid')
    data = json.dumps(reports, ensure_ascii=False).replace('<', '\\u003c')
    name = datetime.now(TZ).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8] + '.html'
    path = private / 'search' / 'canvases' / name
    atomic_write(path, html.replace(marker, marker.replace('null', data, 1)))
    return {'status': 'ok', 'reports': len(reports), 'claims': sum(len(r['claims']) for r in reports),
            'saved_canvas': str(path)}
