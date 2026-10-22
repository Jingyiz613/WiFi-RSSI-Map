import argparse, copy, csv, json, platform, time
from pathlib import Path
import numpy as np
import torch
from models import CompletionNet, features, observation_mask, ROWS, COLS, PADDED

ROOT = Path(__file__).resolve().parents[1]
KINDS = ['autoencoder', 'unet', 'residual_unet']

def write_csv(path, rows):
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)

def prepare():
    config=json.loads((ROOT/'config.json').read_text(encoding='utf-8'))
    data=np.load(ROOT/'data/maps.npz',allow_pickle=False)
    maps=dict(zip(data['groups'].tolist(),data['maps']))
    train_values=np.concatenate([maps[g][np.isfinite(maps[g])] for g in config['split']['train']])
    norm={'mean_dbm':float(train_values.mean()),'std_db':float(train_values.std()),
          'fit_groups':config['split']['train'],'fit_points':len(train_values)}
    (ROOT/'normalization.json').write_text(json.dumps(norm,indent=2),encoding='utf-8')
    torch.set_num_threads(config['threads'])
    torch.use_deterministic_algorithms(True)
    return config,maps,norm

def batch(cases, maps, norm):
    inputs=[];targets=[];hidden=[]
    for group,mask in cases:
        y=maps[group]
        x,_=features(y,mask,norm['mean_dbm'],norm['std_db'])
        target=np.zeros(PADDED,np.float32)
        target[:ROWS,:COLS]=np.nan_to_num((y-norm['mean_dbm'])/norm['std_db'])
        h=np.zeros(PADDED,np.float32)
        h[:ROWS,:COLS]=np.isfinite(y)&~mask
        assert not np.any(h[:ROWS,:COLS].astype(bool)&mask)
        inputs.append(x);targets.append(target[None]);hidden.append(h[None])
    return tuple(torch.from_numpy(np.stack(items)) for items in [inputs,targets,hidden])

def evaluation_cases(config,maps):
    tasks=[]
    for split in ['validation','test']:
        for group in config['split'][split]:
            for budget in config['input_budgets']:
                for distribution in config['input_distributions']:
                    for repeat in range(config['mask_repeats']):
                        seed=config['seed']+10000*int(group[1:])+100*budget+10*repeat+(distribution=='clustered')
                        mask=observation_mask(maps[group],budget,distribution,np.random.default_rng(seed))
                        tasks.append({'split':split,'group':group,'budget':budget,'distribution':distribution,
                                      'repeat':repeat,'mask':mask})
    np.savez_compressed(ROOT/'data/evaluation_masks.npz',
                        masks=np.stack([t['mask'] for t in tasks]),
                        split=np.array([t['split'] for t in tasks]),groups=np.array([t['group'] for t in tasks]),
                        budgets=np.array([t['budget'] for t in tasks]),
                        distributions=np.array([t['distribution'] for t in tasks]),
                        repeats=np.array([t['repeat'] for t in tasks]))
    return tasks

def validation_loss(model, vb):
    model.eval();total=0.;count=0.
    with torch.inference_mode():
        for start in range(0,len(vb[0]),32):
            x,y,h=[a[start:start+32] for a in vb]
            total+=float(((model(x)-y).square()*h).sum());count+=float(h.sum())
    return total/count

def smoke_checks(maps,norm):
    rng=np.random.default_rng(1);y=maps['P16'];mask=observation_mask(y,12,'uniform',rng)
    x,_=features(y,mask,norm['mean_dbm'],norm['std_db'])
    changed=y.copy();changed[~mask]=12345
    x2,_=features(changed,mask,norm['mean_dbm'],norm['std_db'])
    assert np.array_equal(x,x2),'Hidden targets leaked into input or IDW baseline'
    b=batch([('P16',mask)],maps,norm)
    torch.manual_seed(1);model=CompletionNet('unet');opt=torch.optim.Adam(model.parameters(),lr=.003)
    losses=[]
    for _ in range(40):
        opt.zero_grad();pred=model(b[0]);loss=((pred-b[1]).square()*b[2]).sum()/b[2].sum()
        loss.backward();opt.step();losses.append(float(loss.detach()))
    assert losses[-1]<losses[0]*.8,losses
    (ROOT/'smoke_check.json').write_text(json.dumps({'hidden_label_invariance':'PASS',
        'fixed_case_initial_mse':losses[0],'fixed_case_final_mse':losses[-1]},indent=2),encoding='utf-8')

def main():
    config,maps,norm=prepare();(ROOT/'models').mkdir(exist_ok=True)
    smoke_checks(maps,norm)
    tasks=evaluation_cases(config,maps)
    validation=[t for t in tasks if t['split']=='validation']
    vb=batch([(t['group'],t['mask']) for t in validation],maps,norm)
    histories=[];runs=[]
    for kind in KINDS:
        for seed in config['model_seeds']:
            torch.manual_seed(seed);rng=np.random.default_rng(seed)
            model=CompletionNet(kind)
            optimizer=torch.optim.AdamW(model.parameters(),lr=config['lr'],weight_decay=config['weight_decay'])
            best=float('inf');best_state=None;stale=0;started=time.perf_counter();best_epoch=0
            for epoch in range(1,config['max_epochs']+1):
                model.train();loss_sum=0.
                for _ in range(config['steps_per_epoch']):
                    cases=[]
                    for _ in range(config['batch_size']):
                        group=rng.choice(config['split']['train'])
                        budget=int(rng.choice(config['input_budgets']))
                        distribution=rng.choice(config['input_distributions'])
                        cases.append((group,observation_mask(maps[group],budget,distribution,rng)))
                    x,y,h=batch(cases,maps,norm)
                    optimizer.zero_grad(set_to_none=True)
                    loss=((model(x)-y).square()*h).sum()/h.sum()
                    assert torch.isfinite(loss)
                    loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),5);optimizer.step()
                    loss_sum+=float(loss.detach())
                if epoch==1 or epoch%config['validate_every']==0:
                    val=validation_loss(model,vb)
                    histories.append({'model':kind,'seed':seed,'epoch':epoch,
                                      'train_rmse_db':(loss_sum/config['steps_per_epoch'])**.5*norm['std_db'],
                                      'validation_rmse_db':val**.5*norm['std_db']})
                    if val<best:
                        best=val;best_state=copy.deepcopy(model.state_dict());best_epoch=epoch;stale=0
                    else:stale+=1
                    if epoch==1 or epoch%30==0:
                        print(f'{kind} seed={seed} epoch={epoch} val_RMSE={val**.5*norm["std_db"]:.3f} dB',flush=True)
                    if stale>=config['patience_checks']:break
            checkpoint={'kind':kind,'seed':seed,'state_dict':best_state,'normalization':norm,
                        'best_epoch':best_epoch,'best_validation_rmse_db':best**.5*norm['std_db'],
                        'config':config}
            path=ROOT/'models'/f'{kind}_{seed}.pt';torch.save(checkpoint,path)
            runs.append({'model':kind,'seed':seed,'best_epoch':best_epoch,'epochs_run':epoch,
                         'validation_rmse_db':checkpoint['best_validation_rmse_db'],
                         'train_seconds':time.perf_counter()-started,
                         'parameters':sum(p.numel() for p in model.parameters()),'weight_bytes':path.stat().st_size})
            write_csv(ROOT/'training_curves.csv',histories);write_csv(ROOT/'training_runs.csv',runs)
            print('SAVED',path.name,'best epoch',best_epoch,flush=True)
    summary={kind:float(np.mean([r['validation_rmse_db'] for r in runs if r['model']==kind])) for kind in KINDS}
    selected_kind=min(summary,key=summary.get)
    selected_run=min([r for r in runs if r['model']==selected_kind],key=lambda r:r['validation_rmse_db'])
    selection={'selected_neural_model':selected_kind,'selected_seed':selected_run['seed'],
               'selection_rule':'architecture by mean validation RMSE across 3 seeds; seed by validation RMSE',
               'validation_rmse_by_architecture':summary,'test_used_for_selection':False}
    (ROOT/'selection.json').write_text(json.dumps(selection,indent=2),encoding='utf-8')
    (ROOT/'environment.json').write_text(json.dumps({'python':platform.python_version(),'torch':torch.__version__,
        'numpy':np.__version__,'platform':platform.platform(),'processor':platform.processor(),
        'threads':torch.get_num_threads(),'device':'cpu'},indent=2),encoding='utf-8')
    print('TRAINING COMPLETE',json.dumps(selection),flush=True)

if __name__=='__main__':main()
