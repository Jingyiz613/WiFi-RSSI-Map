import csv, json, time
from pathlib import Path
import numpy as np
import torch
from models import CompletionNet, features, ROWS, COLS
from train import prepare, batch, write_csv

ROOT=Path(__file__).resolve().parents[1]

def metrics(truth,prediction):
    error=np.asarray(prediction)-np.asarray(truth);absolute=np.abs(error)
    denominator=float(((truth-np.mean(truth))**2).sum())
    return {'mae_db':float(absolute.mean()),'rmse_db':float(np.sqrt(np.mean(error**2))),
            'bias_db':float(error.mean()),'p90_abs_error_db':float(np.quantile(absolute,.9)),
            'within_3db_pct':float(np.mean(absolute<=3)*100),
            'within_5db_pct':float(np.mean(absolute<=5)*100),
            'r2':float(1-np.sum(error**2)/denominator) if denominator>0 else None,
            'hidden_predictions':len(error)}

def main():
    config,maps,norm=prepare()
    saved=np.load(ROOT/'data/evaluation_masks.npz',allow_pickle=False)
    tasks=[{'split':str(saved['split'][i]),'group':str(saved['groups'][i]),
            'budget':int(saved['budgets'][i]),'distribution':str(saved['distributions'][i]),
            'repeat':int(saved['repeats'][i]),'mask':saved['masks'][i]} for i in range(len(saved['masks']))]
    inputs=batch([(t['group'],t['mask']) for t in tasks],maps,norm)[0]
    preds={};case_rows=[];aggregate=[];timings=[]
    model_metadata=[]
    for kind in ['idw','autoencoder','unet','residual_unet']:
        for seed in ([0] if kind=='idw' else config['model_seeds']):
            key=f'{kind}_{seed}'
            if kind=='idw':
                prediction=np.stack([features(maps[t['group']],t['mask'],norm['mean_dbm'],norm['std_db'])[1] for t in tasks])
                model=None;parameter_count=0;weight_bytes=0
            else:
                checkpoint_path=ROOT/'models'/f'{key}.pt'
                checkpoint=torch.load(checkpoint_path,map_location='cpu',weights_only=True)
                model=CompletionNet(kind);model.load_state_dict(checkpoint['state_dict']);model.eval()
                assert checkpoint['normalization']==norm
                with torch.inference_mode():
                    chunks=[model(inputs[start:start+32])[:,0,:ROWS,:COLS].numpy() for start in range(0,len(inputs),32)]
                prediction=np.concatenate(chunks)*norm['std_db']+norm['mean_dbm']
                parameter_count=sum(p.numel() for p in model.parameters());weight_bytes=checkpoint_path.stat().st_size
            assert np.isfinite(prediction).all()
            preds[key]=prediction
            for i,t in enumerate(tasks):
                hidden=np.isfinite(maps[t['group']])&~t['mask']
                score=metrics(maps[t['group']][hidden],prediction[i][hidden])
                case_rows.append({k:v for k,v in {'model':kind,'seed':seed,'task_index':i,**t,**score}.items() if k!='mask'})
            for split in ['validation','test']:
                for budget in config['input_budgets']:
                    for distribution in config['input_distributions']:
                        indices=[i for i,t in enumerate(tasks) if (t['split'],t['budget'],t['distribution'])==(split,budget,distribution)]
                        truths=[];estimates=[]
                        for i in indices:
                            hidden=np.isfinite(maps[tasks[i]['group']])&~tasks[i]['mask']
                            truths.append(maps[tasks[i]['group']][hidden]);estimates.append(prediction[i][hidden])
                        aggregate.append({'model':kind,'seed':seed,'split':split,'budget':budget,'distribution':distribution,
                                          'mask_cases':len(indices),**metrics(np.concatenate(truths),np.concatenate(estimates))})
            # Single-map end-to-end CPU time includes common feature/IDW construction, normalization,
            # tensor conversion, neural forward (if applicable), and de-normalization. No file I/O.
            t=next(t for t in tasks if t['split']=='test' and t['budget']==12 and t['distribution']=='uniform')
            forward_times=[];full_times=[]
            with torch.inference_mode():
                for repeat in range(55):
                    start=time.perf_counter()
                    x,b=features(maps[t['group']],t['mask'],norm['mean_dbm'],norm['std_db'])
                    tensor=torch.from_numpy(x[None])
                    forward_start=time.perf_counter()
                    result=b if model is None else model(tensor)[:,0,:ROWS,:COLS].numpy()*norm['std_db']+norm['mean_dbm']
                    stop=time.perf_counter()
                    if repeat>=5:
                        full_times.append((stop-start)*1000)
                        forward_times.append((stop-forward_start)*1000)
            timings.append({'model':kind,'seed':seed,'cpu_e2e_median_ms':float(np.median(full_times)),
                            'cpu_e2e_p95_ms':float(np.quantile(full_times,.95)),
                            'forward_median_ms':float(np.median(forward_times)),
                            'parameters':parameter_count,'weight_bytes':weight_bytes})
    write_csv(ROOT/'metrics_by_seed.csv',aggregate);write_csv(ROOT/'metrics_per_mask.csv',case_rows)
    write_csv(ROOT/'inference_timing.csv',timings)
    np.savez_compressed(ROOT/'predictions.npz',**preds)
    summary=[]
    numeric=['mae_db','rmse_db','bias_db','p90_abs_error_db','within_3db_pct','within_5db_pct','r2']
    for split in ['validation','test']:
        for budget in config['input_budgets']:
            for distribution in config['input_distributions']:
                for kind in ['idw','autoencoder','unet','residual_unet']:
                    rows=[r for r in aggregate if (r['split'],r['budget'],r['distribution'],r['model'])==(split,budget,distribution,kind)]
                    result={'split':split,'budget':budget,'distribution':distribution,'model':kind,
                            'training_seeds':len(rows),'mask_cases_per_seed':rows[0]['mask_cases'],
                            'hidden_predictions_per_seed':rows[0]['hidden_predictions']}
                    for metric in numeric:
                        values=[r[metric] for r in rows]
                        result[metric]=float(np.mean(values));result[metric+'_seed_std']=float(np.std(values,ddof=1)) if len(values)>1 else 0.
                    result['cpu_e2e_median_ms']=float(np.mean([r['cpu_e2e_median_ms'] for r in timings if r['model']==kind]))
                    baseline=next(r for r in aggregate if (r['split'],r['budget'],r['distribution'],r['model'])==(split,budget,distribution,'idw'))
                    result['rmse_improvement_vs_idw_pct']=(baseline['rmse_db']-result['rmse_db'])/baseline['rmse_db']*100
                    summary.append(result)
    write_csv(ROOT/'metrics_summary.csv',summary)
    selection=json.loads((ROOT/'selection.json').read_text())
    selected=f'{selection["selected_neural_model"]}_{selection["selected_seed"]}'
    example_index=next(i for i,t in enumerate(tasks) if t['split']=='test' and t['budget']==12 and t['distribution']=='uniform' and t['repeat']==0)
    example=tasks[example_index];target=maps[example['group']]
    points=[]
    for r,c in np.argwhere(example['mask']):
        points.append({'point_id':f'P{r+1}{c+1}','rssi_dbm':float(target[r,c])})
    write_csv(ROOT/'demo_inputs.csv',points)
    demo=preds[selected][example_index]
    np.savez_compressed(ROOT/'demo_reference_for_evaluation_only.npz',truth=target,
                        observed=example['mask'],prediction=demo,idw=preds['idw_0'][example_index])
    (ROOT/'demo_case.json').write_text(json.dumps({'group':example['group'],'budget':12,'distribution':'uniform',
        'repeat':0,'selected':selected,'task_index':example_index},indent=2),encoding='utf-8')
    # Paired bootstrap describes sensitivity to masks on this fixed map only; not cross-scene confidence.
    intervals=[];rng=np.random.default_rng(config['seed'])
    for budget in config['input_budgets']:
        for distribution in config['input_distributions']:
            base=[r for r in case_rows if (r['split'],r['budget'],r['distribution'],r['model'])==('test',budget,distribution,'idw')]
            differences=[]
            for row in base:
                matching=[r['rmse_db'] for r in case_rows if r['task_index']==row['task_index'] and r['model']==selection['selected_neural_model']]
                differences.append(row['rmse_db']-np.mean(matching))
            samples=np.mean(rng.choice(differences,size=(2000,len(differences)),replace=True),axis=1)
            intervals.append({'budget':budget,'distribution':distribution,'model':selection['selected_neural_model'],
                              'mean_per_mask_rmse_improvement_db':float(np.mean(differences)),
                              'mask_bootstrap_95_low_db':float(np.quantile(samples,.025)),
                              'mask_bootstrap_95_high_db':float(np.quantile(samples,.975)),
                              'scope':'fixed P74 map and these 10 masks only; no independent-scene confidence claim'})
    write_csv(ROOT/'mask_sensitivity_intervals.csv',intervals)
    print('TEST RESULTS (12 uniform input points; mean across training seeds):',flush=True)
    for row in summary:
        if (row['split'],row['budget'],row['distribution'])==('test',12,'uniform'):
            print(json.dumps(row),flush=True)
    print('EVALUATION COMPLETE',flush=True)

if __name__=='__main__':main()
