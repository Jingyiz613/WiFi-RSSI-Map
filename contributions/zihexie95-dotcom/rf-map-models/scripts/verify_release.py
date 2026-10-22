"""Small meaningful release checks: inference parity, splits, and safe planning."""
import csv
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gpr/code'))
from gpr import XY, ap_position, predict, self_check
from assistant_tools import analyze_observations

self_check()
protocol = json.loads((ROOT/'protocol.json').read_text(encoding='utf-8'))
splits = [set(protocol['split'][s]) for s in ['train','validation','test']]
assert all(not splits[a]&splits[b] for a,b in [(0,1),(0,2),(1,2)])
data = np.load(ROOT/'neural/data/maps.npz', allow_pickle=False)
assert data['maps'].shape == (8,7,9) and np.isfinite(data['maps']).sum() == 495
assert set(data['groups'].tolist()) == set.union(*splits)
with (ROOT/'examples/demo_inputs.csv').open(encoding='utf-8-sig',newline='') as stream:
    observations = [{'point_id':r['point_id'], 'rssi_dbm':float(r['rssi_dbm'])} for r in csv.DictReader(stream)]
result = analyze_observations('P74', observations, 3)
reversed_result = analyze_observations('P74', list(reversed(observations)), 3)
suggested = [r['point_id'] for r in result['suggested_measurements']]
assert suggested == [r['point_id'] for r in reversed_result['suggested_measurements']]
assert len(suggested) == len(set(suggested)) == 3
assert not set(suggested)&({r['point_id'] for r in observations}|{'P74'})
constrained = analyze_observations('P74', observations, 3, suggested)
assert not {r['point_id'] for r in constrained['suggested_measurements']}&set(suggested)
all_points = [f'P{i//9+1}{i%9+1}' for i in range(63)]
assert analyze_observations('P74', observations, 3, all_points)['suggested_measurements'] == []
model = json.loads((ROOT/'gpr/models/pathloss_gpr.json').read_text(encoding='utf-8'))
means,_ = predict(model, np.array([ap_position(r['point_id']) for r in observations]),
                  np.array([r['rssi_dbm'] for r in observations]), XY, ap_position('P74'))
with (ROOT/'examples/demo_gpr_output.csv').open(encoding='utf-8-sig',newline='') as stream:
    reference = list(csv.DictReader(stream))
for i,row in enumerate(reference):
    if row['source'] == 'predicted':
        assert abs(means[i]-float(row['rssi_dbm'])) < 1e-8
for bad in [observations+[observations[0]], observations+[{'point_id':'P74','rssi_dbm':-40}],
            [{'point_id':'P11','rssi_dbm':float('nan')}]]:
    try:
        analyze_observations('P74', bad)
    except ValueError:
        pass
    else:
        raise AssertionError('Invalid input accepted')
assert analyze_observations('P74',[{'point_id':f'P{i//9+1}{i%9+1}','rssi_dbm':-40}
       for i in range(63) if f'P{i//9+1}{i%9+1}'!='P74'])['suggested_measurements'] == []
print('PASS: disjoint splits, 495 labels, saved GP inference parity, planner order invariance, candidate exclusions, invalid inputs.')
traces_path = ROOT/'results/active_sampling/selection_traces.json'
if traces_path.exists():
    traces = json.loads(traces_path.read_text(encoding='utf-8'))
    assert len(traces)==2*20*3*3
    cases={}
    for trace in traces:
        observed=trace['observed_points']; heldout=trace['evaluation_points']
        assert len(observed)==len(set(observed))==trace['budget']
        assert len(heldout)==len(set(heldout))==20 and not set(observed)&set(heldout)
        cases.setdefault((trace['ap'],trace['repeat']),[]).append(trace)
    for rows in cases.values():
        assert all(r['evaluation_points']==rows[0]['evaluation_points'] for r in rows)
        assert all(r['observed_points'][:6]==rows[0]['observed_points'][:6] for r in rows)
        for strategy in ['random','space_filling','gp_variance']:
            sequence=sorted([r for r in rows if r['strategy']==strategy],key=lambda r:r['budget'])
            assert sequence[2]['observed_points'][:12]==sequence[1]['observed_points']
    print('PASS: blocked regions, fixed holdouts, shared initial observations, nested budgets across 360 replay cases.')
