"""Deterministic local tools; no remote LLM, raw map labels, or arbitrary commands."""
import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gpr/code'))
from gpr import XY, ap_position, predict


def analyze_observations(ap, observations, count=3, excluded_points=None):
    """Predict from observations only and greedily propose unmeasured locations."""
    if not isinstance(ap, str):
        raise ValueError('AP must be a point label')
    ap = ap.upper()
    ap_xy = ap_position(ap)
    excluded_points = [] if excluded_points is None else excluded_points
    if not isinstance(excluded_points, list) or len(excluded_points) > 63:
        raise ValueError('excluded_points must be a list of at most 63 grid labels')
    blocked = set()
    for point in excluded_points:
        if not isinstance(point, str):
            raise ValueError('Excluded points must be grid labels')
        ap_position(point.upper())
        blocked.add(point.upper())
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 10:
        raise ValueError('count must be an integer between 1 and 10')
    if not isinstance(observations, list) or not 1 <= len(observations) <= 62:
        raise ValueError('Supply 1 to 62 observations')
    seen = {ap}
    points, values = [], []
    for item in observations:
        point = item['point_id'].upper()
        xy = ap_position(point)
        value = item['rssi_dbm']
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value) or not -127 <= value < 0:
            raise ValueError('RSSI must be finite and in [-127, 0)')
        if point in seen:
            raise ValueError('Duplicate point or occupied AP cell: ' + point)
        seen.add(point)
        points.append(xy)
        values.append(float(value))
    model = json.loads((ROOT / 'gpr/models/pathloss_gpr.json').read_text(encoding='utf-8'))
    points, values = np.asarray(points), np.asarray(values)
    means, variance = predict(model, points, values, XY, ap_xy)
    ids = [f'P{i//9+1}{i%9+1}' for i in range(63)]
    lookup = {item['point_id'].upper(): float(item['rssi_dbm']) for item in observations}
    labels = ['极差', '较差', '一般', '较好', '极好']
    rows = []
    for i, point in enumerate(ids):
        value = None if point == ap else lookup.get(point, float(means[i]))
        rows.append({'point_id': point, 'source': 'ap' if point == ap else 'observed' if point in lookup else 'predicted',
                     'rssi_dbm': value, 'relative_grade': None if value is None else labels[int(np.digitize(value, [-40,-35,-30,-25]))],
                     'prediction_sd_db': float(np.sqrt(variance[i])) if point not in seen else None})
    candidates = [i for i, point in enumerate(ids) if point not in seen and point not in blocked]
    suggestions = []
    for _ in range(min(count, len(candidates))):
        temporary_means, temporary_variance = predict(model, points, values, XY[candidates], ap_xy)
        local = int(np.argmax(temporary_variance))
        index = candidates.pop(local)
        suggestions.append({'point_id': ids[index], 'x_m': float(XY[index,0]), 'y_m': float(XY[index,1]),
                            'conditional_prediction_sd_db': float(np.sqrt(temporary_variance[local])),
                            'reason': 'maximum remaining conditional GP variance'})
        # Fantasy means are used only to condition the planning covariance, never as real labels.
        points = np.vstack([points, XY[index]])
        values = np.append(values, temporary_means[local])
    return {'model': 'pathloss_gpr', 'ap': ap, 'observed_count': len(observations),
            'excluded_points': sorted(blocked),
            'grades': 'experiment-relative thresholds: -40/-35/-30/-25 dBm',
            'map': rows, 'suggested_measurements': suggestions,
            'limitation': 'Model-based planning only; field benefit and uncertainty calibration not independently validated.'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--points', type=Path, required=True)
    parser.add_argument('--ap', required=True)
    parser.add_argument('--count', type=int, default=3)
    parser.add_argument('--exclude', nargs='*', default=[], help='Grid cells unavailable for further measurements')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    with args.points.open(encoding='utf-8-sig', newline='') as stream:
        observations = [{'point_id': r['point_id'], 'rssi_dbm': float(r['rssi_dbm'])} for r in csv.DictReader(stream)]
    result = analyze_observations(args.ap, observations, args.count, args.exclude)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'suggested_measurements': result['suggested_measurements']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
