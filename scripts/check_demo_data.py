"""Audit existing snapshots read-only. Live collection requires two explicit flags."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
import sqlite3
import sys
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def audit(db_path, collection_ids=(), required_sources=(), export_dir=None):
    report = {'mode': 'local-read-only', 'database': str(db_path.resolve()),
              'created_at': datetime.now(timezone.utc).isoformat(), 'collections': [], 'errors': []}
    if not db_path.is_file():
        report['errors'].append('Database does not exist; nothing was created.')
        report['ready'] = False
        return report
    with sqlite3.connect(db_path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            report['errors'].append('SQLite integrity check failed.')
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'collections', 'collection_observations', 'collection_artifacts', 'collection_grids', 'collection_quality_issues'} <= tables:
            report['errors'].append('Snapshot schema is missing.')
            report['ready'] = False
            return report
        rows = db.execute('SELECT * FROM collections ORDER BY created_at DESC').fetchall()
        known = {r['id'] for r in rows}
        for missing in set(collection_ids) - known:
            report['errors'].append('Unknown collection: ' + missing)
        for row in rows:
            if collection_ids and row['id'] not in collection_ids:
                continue
            meta, summary = json.loads(row['metadata']), json.loads(row['summary'])
            cid = row['id']
            item = {'id': cid, 'status': row['status'], 'metadata': meta, 'summary': summary,
                    'errors': [], 'artifacts': []}
            counts = dict(db.execute('SELECT variable,COUNT(*) FROM collection_observations WHERE collection_id=? GROUP BY variable', (cid,)))
            if sum(counts.values()) != summary.get('imported_values', 0):
                item['errors'].append('Imported count does not match snapshot rows.')
            if counts != summary.get('counts_by_variable', {}):
                item['errors'].append('Per-variable counts do not match summary.')
            from backend.app.domain.wind import DERIVED_WIND_UNITS, WIND_COMPONENTS
            primary = meta['primary_variable']
            components = WIND_COMPONENTS if primary in DERIVED_WIND_UNITS else [primary]
            start = datetime.fromisoformat(meta['requested_start_utc'])
            end = datetime.fromisoformat(meta['requested_end_utc'])
            hours = int((end - start).total_seconds() // 3600) + 1
            grids = list(db.execute('SELECT latitude,longitude FROM collection_grids WHERE collection_id=?', (cid,)))
            available = 0
            for grid in grids:
                times = []
                for variable in components:
                    times.append({r[0] for r in db.execute('''SELECT time_utc FROM collection_observations
                        WHERE collection_id=? AND variable=? AND grid_latitude=? AND grid_longitude=?
                        AND time_utc>=? AND time_utc<=?''',
                        (cid, variable, *grid, meta['requested_start_utc'], meta['requested_end_utc']))})
                available += len(set.intersection(*times))
            expected = hours * max(1, len(grids))
            item['verified_coverage'] = dict(available=available, expected=expected)
            if (available, expected) != (summary.get('available_count'), summary.get('expected_count')):
                item['errors'].append('Requested coverage does not match database.')
            if row['status'] != 'complete' or available != expected:
                item['errors'].append('Collection is not complete enough for a ready demo.')
            if meta.get('source') not in {'land', 'era5'}:
                item['errors'].append('This is not a real ERA5/Land collection.')
            for artifact in db.execute('SELECT * FROM collection_artifacts WHERE collection_id=?', (cid,)):
                path = Path(artifact['path'])
                valid = False
                if path.is_file():
                    digest = sha256()
                    with path.open('rb') as handle:
                        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                            digest.update(chunk)
                    valid = digest.hexdigest() == artifact['sha256'] and path.stat().st_size == artifact['bytes']
                item['artifacts'].append({'path': str(path), 'verified': valid})
                if not valid:
                    item['errors'].append('Missing or modified source file: ' + str(path))
            if not item['artifacts']:
                item['errors'].append('No source artifact recorded.')
            issues = db.execute('SELECT COUNT(*) FROM collection_quality_issues WHERE collection_id=?', (cid,)).fetchone()[0]
            item['quality_issue_count'] = issues
            quality = summary.get('quality') or {}
            if quality.get('issue_count', issues) != issues:
                item['errors'].append('Quality issue count does not match summary.')
            for value, dataset, unit in db.execute('SELECT value,dataset,unit FROM collection_observations WHERE collection_id=?', (cid,)):
                if value is None or not isfinite(value) or dataset != meta['dataset'] or not unit:
                    item['errors'].append('Invalid value, dataset or unit in snapshot.')
                    break
            observations = db.execute('SELECT * FROM collection_observations WHERE collection_id=? ORDER BY time_utc,variable', (cid,))
            if export_dir:
                export_dir.mkdir(parents=True, exist_ok=True)
                with (export_dir / (cid + '.csv')).open('w', encoding='utf-8-sig', newline='') as handle:
                    writer = csv.writer(handle)
                    writer.writerow(['collection_id', 'dataset', 'variable', 'grid_latitude', 'grid_longitude', 'time_utc', 'unit', 'value', 'source'])
                    for obs in observations:
                        if not isfinite(obs['value']):
                            item['errors'].append('Non-finite observation.')
                        writer.writerow([*obs, meta['source']])
            report['collections'].append(item)
    present = {c['metadata']['source'] for c in report['collections'] if not c['errors']}
    for source in set(required_sources) - present:
        report['errors'].append('No verified complete collection for source: ' + source)
    if not report['collections']:
        report['errors'].append('No selected collections found.')
    report['ready'] = not report['errors'] and all(not c['errors'] for c in report['collections'])
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=ROOT / 'data/cache/climate.sqlite3')
    parser.add_argument('--collection', action='append', default=[])
    parser.add_argument('--require-source', choices=['land', 'era5'], action='append', default=[])
    parser.add_argument('--export-dir', type=Path)
    parser.add_argument('--log', type=Path, default=ROOT / 'output/phase4' / (datetime.now().strftime('%Y%m%d-%H%M%S') + '.json'))
    parser.add_argument('--allow-live', action='store_true')
    parser.add_argument('--live-request', type=Path, help='JSON DownloadRequest; requires --allow-live')
    parser.add_argument('--api-url', default='http://localhost:8000')
    args = parser.parse_args(argv)
    if args.allow_live != bool(args.live_request):
        parser.error('Real collection requires both --allow-live and --live-request; no request sent.')
    try:
        if args.allow_live:
            address = urllib.parse.urlparse(args.api_url)
            if address.scheme != 'http' or address.hostname not in {'localhost', '127.0.0.1', '::1'}:
                parser.error('--api-url must be a local HTTP backend.')
            from backend.app.domain.schemas import DownloadRequest
            request = DownloadRequest.model_validate_json(args.live_request.read_text(encoding='utf-8'))
            if request.source not in {'land', 'era5'}:
                parser.error('Explicit source land or era5 is required.')
            payload = json.dumps(request.model_dump(mode='json')).encode()
            req = urllib.request.Request(args.api_url.rstrip('/') + '/api/v1/downloads', payload,
                                         {'Content-Type': 'application/json'}, method='POST')
            with urllib.request.urlopen(req, timeout=30) as response:
                job = json.load(response)
            report = {'mode': 'live-submission-only', 'ready': False, 'job': job,
                      'message': 'Job accepted, NOT verified complete. Run local audit after completion.'}
            code = 2
        else:
            report = audit(args.db, args.collection, args.require_source, args.export_dir)
            code = 0 if report['ready'] else 1
    except (OSError, ValueError, sqlite3.Error, KeyError) as exc:
        report = {'ready': False, 'error_type': type(exc).__name__, 'message': 'Audit/submission failed; inspect local configuration and backend logs.'}
        code = 1
    args.log.parent.mkdir(parents=True, exist_ok=True)
    args.log.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'ready': report['ready'], 'log': str(args.log), 'exit_code': code}))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
