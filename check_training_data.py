"""Read-only check of the prepared training data."""
from pathlib import Path
import csv
import math
from collections import Counter


def main():
    source = Path(__file__).resolve().parent / 'output/training_tables_v1/training_points.csv'
    with source.open(encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    expected = {'P22': 62, 'P68': 62, 'P64': 62, 'P44': 62,
                'P28': 62, 'P62': 61, 'P16': 62, 'P49': 62, 'P26': 62}
    counts = Counter(r['ap_point'] for r in rows)
    assert counts == Counter(expected), f'Unexpected scene counts: {counts}'
    assert len({(r['scene_id'], r['point_id']) for r in rows}) == len(rows)
    for r in rows:
        assert r['eligible'] == '1'
        assert r['point_id'] != r['ap_point']
        assert math.isfinite(float(r['rssi_median_dbm']))
        assert int(r['valid_samples']) > 0
        assert 1 <= int(r['row']) <= 7 and 1 <= int(r['column']) <= 9
        assert r['split'] == ('validation' if r['ap_point'] == 'P64' else
                              'test' if r['ap_point'] in ('P49', 'P26') else 'train')
    assert not any(r['ap_point'] == 'P62' and r['point_id'] == 'P11' for r in rows)
    print('DATA CHECK PASSED')
    print(f'Scenes: {len(counts)} | Usable points: {len(rows)}')
    for ap, count in counts.items():
        print(f'  AP {ap}: {count} points')
    for split, count in Counter(r['split'] for r in rows).items():
        print(f'{split}: {count} points')
    print('P62 special records excluded correctly.')
    print('No training was started; source files unchanged.')


if __name__ == '__main__':
    main()
