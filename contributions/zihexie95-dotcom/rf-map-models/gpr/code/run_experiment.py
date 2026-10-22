import csv, hashlib, itertools, json, time
from pathlib import Path
import numpy as np
from gpr import XY, ap_position, path_mean, predict, training_reml, self_check

ROOT=Path(__file__).resolve().parents[1]

def write_csv(path,rows):
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)

def metrics(truth,prediction,variance=None):
    truth=np.asarray(truth);error=np.asarray(prediction)-truth;absolute=np.abs(error)
    sst=float(((truth-truth.mean())**2).sum())
    score={'mae_db':float(absolute.mean()),'rmse_db':float(np.sqrt(np.mean(error**2))),
           'bias_db':float(error.mean()),'p90_abs_error_db':float(np.quantile(absolute,.9)),
           'within_3db_pct':float((absolute<=3).mean()*100),'within_5db_pct':float((absolute<=5).mean()*100),
           'r2':float(1-(error**2).sum()/sst),'hidden_predictions':len(truth),
           'interval95_coverage_pct':'','interval95_mean_width_db':''}
    if variance is not None:
        radius=1.96*np.sqrt(variance)
        score.update(interval95_coverage_pct=float((absolute<=radius).mean()*100),
                     interval95_mean_width_db=float((2*radius).mean()))
    return score

def main():
    started=time.perf_counter();self_check()
    protocol=json.loads((ROOT/'protocol.json').read_text())
    data=np.load(ROOT/'data/maps.npz',allow_pickle=False)
    maps=dict(zip(data['groups'].tolist(),data['maps']))
    saved=np.load(ROOT/'data/evaluation_masks.npz',allow_pickle=False)
    tasks=[{'split':str(saved['split'][i]),'group':str(saved['groups'][i]),'budget':int(saved['budgets'][i]),
            'distribution':str(saved['distributions'][i]),'repeat':int(saved['repeats'][i]),'mask':saved['masks'][i]}
           for i in range(len(saved['masks']))]
    for task in tasks:
        y=maps[task['group']].ravel();observed=task['mask'].ravel()
        assert observed.sum()==task['budget'] and np.isfinite(y[observed]).all()
    train_groups=protocol['split']['train']
    distance_features=[];labels=[]
    for group in train_groups:
        y=maps[group].ravel();ok=np.isfinite(y)
        log_distance=np.log10(np.maximum(np.linalg.norm(XY[ok]-ap_position(group),axis=1),.5))
        distance_features.extend(np.column_stack([np.ones(ok.sum()),log_distance]));labels.extend(y[ok])
    coefficients=np.linalg.lstsq(np.asarray(distance_features),np.asarray(labels),rcond=None)[0].tolist()
    path_fit={'coefficients':coefficients,'formula':'A + slope * log10(max(distance_m,0.5))',
              'path_loss_exponent':-coefficients[1]/10,'fit_groups':train_groups,'fit_points':len(labels)}
    (ROOT/'pathloss_fit.json').write_text(json.dumps(path_fit,indent=2),encoding='utf-8')
    validation=[task for task in tasks if task['split']=='validation']
    candidates=[];selection={};models={'idw':{'kind':'idw'},'pathloss':{'kind':'pathloss','pathloss_coefficients':coefficients}}
    for kind in ['gpr','pathloss_gpr']:
        ranked=[]
        for nu,length,signal,noise in itertools.product(protocol['nu'],protocol['length_m'],protocol['signal_db'],protocol['noise_db']):
            parameters={'nu':nu,'length_m':length,'signal_db':signal,'noise_db':noise}
            loss=0.
            for group in train_groups:
                y=maps[group].ravel();ok=np.isfinite(y)
                trend=path_mean(XY[ok],ap_position(group),coefficients) if kind=='pathloss_gpr' else np.zeros(ok.sum())
                loss+=training_reml(XY[ok],y[ok],parameters,trend)
            ranked.append({'kind':kind,**parameters,'training_reml':loss})
        shortlist=[]
        for nu in protocol['nu']:
            shortlist.extend(sorted([row for row in ranked if row['nu']==nu],key=lambda row:row['training_reml'])[:protocol['shortlist_per_nu']])
        for row in ranked:
            row['validation_rmse_db']='';row['shortlisted']=row in shortlist
        best=None
        for row in shortlist:
            model={'kind':kind,'kernel':{key:row[key] for key in ['nu','length_m','signal_db','noise_db']},
                   'pathloss_coefficients':coefficients}
            errors=[]
            for task in validation:
                y=maps[task['group']].ravel();observed=task['mask'].ravel();hidden=np.isfinite(y)&~observed
                result,_=predict(model,XY[observed],y[observed],XY,ap_position(task['group']))
                errors.extend(result[hidden]-y[hidden])
            row['validation_rmse_db']=float(np.sqrt(np.mean(np.square(errors))))
            if best is None or row['validation_rmse_db']<best['validation_rmse_db']:
                best=dict(row);models[kind]=model
        candidates.extend(ranked);selection[kind]=best
        print('VALIDATION SELECTED',kind,json.dumps(best),flush=True)
    (ROOT/'models').mkdir(exist_ok=True)
    for kind,model in models.items():
        model.update(room={'width_m':8.23,'height_m':6.95,'rows':7,'columns':9},
                     test_used_for_hyperparameter_selection=False,
                     note='P74 was a validation group in earlier experiments; this is an exploratory re-split, not a new blind test.')
        (ROOT/'models'/f'{kind}.json').write_text(json.dumps(model,indent=2),encoding='utf-8')
    write_csv(ROOT/'kernel_search.csv',candidates)
    (ROOT/'kernel_selection.json').write_text(json.dumps(selection,indent=2),encoding='utf-8')
    # Test predictions are first produced only after kernel choices are persisted.
    predictions={};variances={};per_case=[];pooled=[];overall=[];timings=[]
    validation_rmse={}
    for kind,model in models.items():
        maps_pred=[];maps_var=[]
        for index,task in enumerate(tasks):
            y=maps[task['group']].ravel();observed=task['mask'].ravel();hidden=np.isfinite(y)&~observed
            result,variance=predict(model,XY[observed],y[observed],XY,ap_position(task['group']))
            assert np.isfinite(result).all()
            maps_pred.append(result.reshape(7,9));maps_var.append(np.full(63,np.nan) if variance is None else variance)
            per_case.append({'model':kind,'task_index':index,**{k:v for k,v in task.items() if k!='mask'},
                             **metrics(y[hidden],result[hidden],None if variance is None else variance[hidden])})
        predictions[kind]=np.stack(maps_pred);variances[kind]=np.stack(maps_var).reshape(-1,7,9)
        for split in ['validation','test']:
            for budget,distribution in itertools.product(protocol['input_budgets'],protocol['input_distributions']):
                truth=[];estimates=[];var=[];case_count=0
                for i,task in enumerate(tasks):
                    if (task['split'],task['budget'],task['distribution'])!=(split,budget,distribution):continue
                    hidden=np.isfinite(maps[task['group']])&~task['mask'];case_count+=1
                    truth.extend(maps[task['group']][hidden]);estimates.extend(predictions[kind][i][hidden]);var.extend(variances[kind][i][hidden])
                pooled.append({'model':kind,'split':split,'budget':budget,'distribution':distribution,'mask_cases':case_count,
                               **metrics(np.array(truth),np.array(estimates),np.array(var) if kind in ['gpr','pathloss_gpr'] else None)})
            truth=[];estimates=[]
            for i,task in enumerate(tasks):
                if task['split']!=split:continue
                hidden=np.isfinite(maps[task['group']])&~task['mask']
                truth.extend(maps[task['group']][hidden]);estimates.extend(predictions[kind][i][hidden])
            overall.append({'model':kind,'split':split,**metrics(np.array(truth),np.array(estimates))})
            if split=='validation':validation_rmse[kind]=overall[-1]['rmse_db']
        demo=next(t for t in tasks if t['split']=='test' and t['budget']==12 and t['distribution']=='uniform')
        y=maps[demo['group']].ravel();observed=demo['mask'].ravel();elapsed=[]
        for repeat in range(55):
            start=time.perf_counter();predict(model,XY[observed],y[observed],XY,ap_position(demo['group']))
            if repeat>=5:elapsed.append((time.perf_counter()-start)*1000)
        timings.append({'model':kind,'cpu_median_ms':float(np.median(elapsed)),'cpu_p95_ms':float(np.quantile(elapsed,.95))})
    for row in pooled:
        baseline=next(r for r in pooled if (r['model'],r['split'],r['budget'],r['distribution'])==('idw',row['split'],row['budget'],row['distribution']))
        row['rmse_improvement_vs_idw_pct']=(baseline['rmse_db']-row['rmse_db'])/baseline['rmse_db']*100
        row['cpu_median_ms']=next(t['cpu_median_ms'] for t in timings if t['model']==row['model'])
    selected=min(validation_rmse,key=validation_rmse.get)
    (ROOT/'selection.json').write_text(json.dumps({'recommended_by_validation':selected,'validation_overall_rmse_db':validation_rmse,
        'test_used_for_selection':False,'test_status':'P74 was a validation group in earlier experiments; this is an exploratory re-split, not a new blind test.'},indent=2),encoding='utf-8')
    write_csv(ROOT/'metrics_summary.csv',pooled);write_csv(ROOT/'metrics_per_mask.csv',per_case)
    write_csv(ROOT/'metrics_overall.csv',overall);write_csv(ROOT/'inference_timing.csv',timings)
    np.savez_compressed(ROOT/'predictions.npz',**predictions)
    np.savez_compressed(ROOT/'predictive_variances.npz',**variances)
    # Regression: the unchanged IDW exactly matches the preceding experiment.
    previous=np.load(ROOT/'data/previous_idw_predictions.npz',allow_pickle=False)['idw']
    assert np.allclose(previous,predictions['idw'],atol=1e-6)
    check={'idw_matches_previous_experiment':True,'max_difference_db':float(np.abs(previous-predictions['idw']).max()),
           'self_checks':'PASS','test_used_for_hyperparameter_selection':False,
           'runtime_seconds':time.perf_counter()-started}
    (ROOT/'verification.json').write_text(json.dumps(check,indent=2),encoding='utf-8')
    for row in pooled:
        if (row['split'],row['budget'],row['distribution'])==('test',12,'uniform'):print('TEST_12_UNIFORM',json.dumps(row),flush=True)
    print('COMPLETE',json.dumps(check),flush=True)

if __name__=='__main__':main()
