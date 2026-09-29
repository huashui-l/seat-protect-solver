"""Rank externally audited runs against a frozen CG integer reference set."""
import argparse
import json
import statistics
from pathlib import Path


def rank(summary, references):
    rows = summary['cases']
    valid = (len(rows) == len(references) and {r['case_id'] for r in rows} == set(references)
             and summary.get('production_python_callback_count') == 0)
    gaps = []
    for row in rows:
        valid &= bool(row.get('complete') and row.get('unassigned') == 0
                      and row.get('external_hard_violations') == 0
                      and row.get('native_hard_violations') == 0
                      and row.get('evaluator_consistent')
                      and row.get('time_protocol_pass')
                      and (row.get('solve_wall_seconds', 10) <= 5 if summary.get('timing_protocol') == 'solver-wall'
                           else row.get('process_wall_seconds', 10) < 5))
        if 'external_score' in row:
            ref = references[row['case_id']]
            gaps.append({'case_id': row['case_id'], 'gap': 100*(ref-row['external_score'])/max(1,abs(ref)),
                         'score': row['external_score'], 'reference': ref, 'reference_type': 'gap_CG_integer',
                         'wall_seconds': row['process_wall_seconds'], 'solve_wall_seconds': row.get('solve_wall_seconds'), 'complete': row['complete'],
                         'unassigned': row.get('unassigned'),
                         'hard_violations': row.get('external_hard_violations'),
                         'evaluator_consistent': row.get('evaluator_consistent')})
    values = [r['gap'] for r in gaps]
    return {'timing_protocol': summary.get('timing_protocol', 'process-wall'), 'valid': valid, 'count': len(rows), 'worst': max(values),
            'mean': statistics.mean(values), 'median': statistics.median(values),
            'le1': sum(x<=1 for x in values), 'le2': sum(x<=2 for x in values),
            'le5': sum(x<=5 for x in values), 'max_wall': max(r['process_wall_seconds'] for r in rows),
            'max_solve_wall': max((r.get('solve_wall_seconds') or 0) for r in rows) or None,
            'cases': sorted(gaps, key=lambda r: -r['gap'])}


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('run',type=Path)
    parser.add_argument('--references',type=Path,default=Path('docs/provenance/cpp5-cg30/references.json'))
    args=parser.parse_args()
    refs={r['case_id']:r['score'] for r in json.loads(args.references.read_text())}
    result=rank(json.loads((args.run/'summary.json').read_text()),refs)
    (args.run/'ranking.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='cases'}))
    print(json.dumps(result['cases'][:8],indent=2))
