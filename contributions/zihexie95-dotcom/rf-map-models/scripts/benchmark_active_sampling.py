"""Offline adaptive-measurement replay with fixed, disjoint evaluation locations."""
import csv
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'gpr/code'))
from gpr import XY, ap_position, predict
from assistant_tools import analyze_observations

OUT = ROOT/'results/active_sampling'
SEED, REPEATS, HOLDOUT = 20261021, 20, 20
BUDGETS = [6, 12, 24]
STRATEGIES = ['random', 'space_filling', 'gp_variance']


def label(index):
    return f'P{index//9+1}{index%9+1}'


def choose_next(strategy, ap, observed, values, candidates, excluded, rng):
    """Only measured labels cross this boundary; no full map or held-out labels."""
    if strategy == 'random':
        return int(rng.choice(candidates))
    if strategy == 'space_filling':
        distances = np.linalg.norm(XY[candidates,None]-XY[observed][None], axis=2)
        return candidates[int(np.argmax(distances.min(axis=1)))]
    observations = [{'point_id':label(i), 'rssi_dbm':float(v)} for i,v in zip(observed,values)]
    result = analyze_observations(ap, observations, 1, [label(i) for i in excluded])
    point = result['suggested_measurements'][0]['point_id']
    index = (int(point[1])-1)*9+int(point[2])-1
    assert index in candidates
    return index


def write_csv(path, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    data = np.load(ROOT/'neural/data/maps.npz', allow_pickle=False)
    maps = dict(zip(data['groups'].tolist(), data['maps']))
    model = json.loads((ROOT/'gpr/models/pathloss_gpr.json').read_text(encoding='utf-8'))
    # Freeze this protocol before the replay; no acquisition-policy tuning on P74.
    protocol = {'seed':SEED, 'repeats':REPEATS, 'budgets':BUDGETS,
                'groups':{'validation':'P62', 'exploratory_test':'P74'},
                'fixed_evaluation_points_per_repeat':HOLDOUT, 'strategies':STRATEGIES,
                'initial_points':'same 6 randomly selected pool points for all strategies',
                'evaluation':'same 20 locations at all budgets and strategies within each repeat; never queried',
                'model_sha256':hashlib.sha256((ROOT/'gpr/models/pathloss_gpr.json').read_bytes()).hexdigest(),
                'scope':'Previously inspected scenes; exploratory replay, not new blind or room-generalization evidence.',
                'uncertainty':'Fixed kernel; posterior conditioning updates the map, not neural-network retraining.',
                'no_claim':'No LLM was executed. No claim that adaptive selection must beat baselines.'}
    (OUT/'protocol.json').write_text(json.dumps(protocol,indent=2),encoding='utf-8')
    metrics, traces, scores = [], [], {}
    for split, ap in protocol['groups'].items():
        truth = maps[ap].ravel()
        valid = np.flatnonzero(np.isfinite(truth))
        for repeat in range(REPEATS):
            permutation = np.random.default_rng(SEED+1000*int(ap[1:])+repeat).permutation(valid)
            evaluation, pool = permutation[:HOLDOUT], permutation[HOLDOUT:]
            initial = pool[:BUDGETS[0]].tolist()
            excluded = sorted(set(range(63))-set(pool.tolist()))
            assert not set(evaluation)&set(pool)
            for strategy in STRATEGIES:
                observed = list(initial)
                rng = np.random.default_rng(SEED+1000*int(ap[1:])+repeat+100000)
                for budget in range(BUDGETS[0], BUDGETS[-1]+1):
                    if budget > BUDGETS[0]:
                        candidates = sorted(set(pool.tolist())-set(observed))
                        selected = choose_next(strategy, ap, observed, truth[observed], candidates, excluded, rng)
                        assert selected not in observed and selected not in evaluation
                        observed.append(selected)
                    if budget not in BUDGETS:
                        continue
                    prediction, variance = predict(model,XY[observed],truth[observed],XY[evaluation],ap_position(ap))
                    error = prediction-truth[evaluation]
                    grade_error = np.digitize(prediction,[-40,-35,-30,-25])-np.digitize(truth[evaluation],[-40,-35,-30,-25])
                    key = (split,ap,strategy,budget)
                    scores.setdefault(key,[]).append((error,grade_error))
                    metrics.append({'split':split,'ap':ap,'repeat':repeat,'strategy':strategy,'budget':budget,
                                    'mae_db':float(np.abs(error).mean()),'rmse_db':float(np.sqrt((error**2).mean())),
                                    'exact_grade_pct':float((grade_error==0).mean()*100),
                                    'within_one_grade_pct':float((np.abs(grade_error)<=1).mean()*100)})
                    traces.append({'split':split,'ap':ap,'repeat':repeat,'strategy':strategy,'budget':budget,
                                   'observed_points':[label(i) for i in observed],
                                   'evaluation_points':[label(i) for i in evaluation]})
    summary=[]
    for (split,ap,strategy,budget), streams in scores.items():
        error=np.concatenate([e for e,g in streams]); grades=np.concatenate([g for e,g in streams])
        summary.append({'split':split,'ap':ap,'strategy':strategy,'budget':budget,
                        'repeats':REPEATS,'repeated_evaluation_predictions':len(error),
                        'mae_db':float(np.abs(error).mean()),'rmse_db':float(np.sqrt((error**2).mean())),
                        'exact_grade_pct':float((grades==0).mean()*100),
                        'within_one_grade_pct':float((np.abs(grades)<=1).mean()*100)})
    write_csv(OUT/'metrics_per_repeat.csv',metrics)
    write_csv(OUT/'metrics_summary.csv',summary)
    (OUT/'selection_traces.json').write_text(json.dumps(traces,indent=2),encoding='utf-8')
    make_report(summary, metrics)
    print(json.dumps([r for r in summary if r['split']=='exploratory_test'],indent=2))


def make_report(summary, metrics):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1,2,figsize=(10,4),layout='constrained')
    for ax,split in zip(axes,['validation','exploratory_test']):
        for strategy in STRATEGIES:
            rows = sorted([r for r in summary if r['split']==split and r['strategy']==strategy],key=lambda r:r['budget'])
            ax.plot([r['budget'] for r in rows],[r['rmse_db'] for r in rows],marker='o',label=strategy)
        ax.set(title='P62 (validation)' if split=='validation' else 'P74 (exploratory test)',
               xlabel='Measured points',ylabel='RMSE on fixed evaluation locations (dB)',xticks=BUDGETS)
        ax.grid(alpha=.25)
        ax.legend(fontsize=8)
    fig.savefig(OUT/'learning_curves.png',dpi=170)
    plt.close(fig)
    report='''# 主动测量离线回放

这是新增的 AI 决策实验：让固定 GPR 模型按当前观测的不确定性选择下一测点，读取该点已有实测值后更新预测。对照为随机选点和最远点空间覆盖策略。三者使用相同预测模型，只比较选点方式。

每组回放 20 次，每次固定留出 20 个评估位置，整个过程中不允许查询；其余位置为候选池，三种策略从相同 6 点起步，增加到 12/24 点。评估位置在同次回放的不同预算和策略间保持不变，避免“把困难点测掉”导致评分虚高。空间覆盖策略是最远点采样，不等同于原实验均匀掩码。

| 组别 | 测点数 | 选点方式 | RMSE（dB） | 五档完全一致 | 相差≤一档 |
|---|---:|---|---:|---:|---:|
'''
    for r in summary:
        report+=f"| {r['ap']} | {r['budget']} | {r['strategy']} | {r['rmse_db']:.3f} | {r['exact_grade_pct']:.1f}% | {r['within_one_grade_pct']:.1f}% |\n"
    report+='\n![选点学习曲线](learning_curves.png)\n\n'
    for ap in ['P62','P74']:
        rows={r['strategy']:r for r in summary if r['ap']==ap and r['budget']==12}
        change=(rows['random']['rmse_db']-rows['gp_variance']['rmse_db'])/rows['random']['rmse_db']*100
        report+=f"{ap} 在 12 点预算下，GP 方差选点相对随机选点的 RMSE 降幅为 {change:.1f}%（负值表示更差）。\n\n"
    report+='''## 如何解读

- 只检验已有模型的方差采样策略，不代表大模型已接入，也不是强化学习。
- GPR 核参数保持固定；每增加一个真实观测，更新后验地图。固定核下方差不依赖观测值，不能把方差策略描述为学习了现场语义或新的选点策略。
- P62 用于既有模型选择；P74 曾在更早实验中作为验证组。这是探索性回放，不能当作新的盲测或跨房间泛化。
- 每个预算 400 次评分是 20 点×20 次回放，位置会重复，不是 400 个独立场景；曲线不保证单调，不保证方差选点优于随机或空间覆盖。
- 这是新的固定留出点评估，与原来“所有未测点评分”的数值不能直接比较。
- `selection_traces.json` 记录每次观测与评估位置。选点器只接收已测值和允许点位，不接收未测 RSSI；评估标签由独立的回放循环保管。

补充工具能力：`assistant_tools.py --exclude P11 P12` 可以排除禁止进入或无法测量的位置。未来大模型可把用户的现场约束转换为点位限制，再调用工具重新规划；这里暂未执行远程大模型。

研究参考：[Bayesian Active Learning for Sample Efficient 5G Radio Map Reconstruction](https://doi.org/10.1109/TWC.2024.3483112)。该论文研究 GP 与主动测量结合；本项目只做简单单 GP 方差基线，不声称复现其集成模型或论文结果。
'''
    (OUT/'主动测量评估.md').write_text(report,encoding='utf-8')


if __name__=='__main__':
    main()
