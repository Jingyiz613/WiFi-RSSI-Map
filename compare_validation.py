"""Compare saved U-Net and IDW on identical P64 inputs; do not evaluate test scenes."""
from pathlib import Path
import csv
import json
import argparse
import math
import torch
from smoke_train import SmallUNet


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--run',default='20261021_134356_519488')
    args=p.parse_args()
    root=Path(__file__).resolve().parent
    run=root/'output/unet_runs'/args.run
    config=json.loads((run/'config.json').read_text(encoding='utf-8'))
    choices=json.loads((run/'validation_inputs.json').read_text(encoding='utf-8'))
    with (root/'output/training_tables_v1/training_points.csv').open(encoding='utf-8-sig',newline='') as f:
        rows=[r for r in csv.DictReader(f) if r['ap_point']=='P64' and r['split']=='validation']
    assert len(rows)==62
    lookup={r['point_id']:r for r in rows}
    device=torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    model=SmallUNet().to(device)
    ckpt=torch.load(run/'best_model.pt',map_location=device,weights_only=True)
    assert ckpt['config']==config
    model.load_state_dict(ckpt['model_state_dict']);model.eval()
    mean,scale=config['mean_dbm'],config['scale_db']
    results=[]
    for trial,ids in enumerate(choices,1):
        assert len(ids)==len(set(ids))==20 and set(ids)<=set(lookup)
        inputs=[lookup[i] for i in ids]
        x=torch.zeros(1,3,7,9);x[:,2]=1
        for r in inputs:
            i,j=int(r['row'])-1,int(r['column'])-1
            x[0,0,i,j]=(float(r['rssi_median_dbm'])-mean)/scale
            x[0,1,i,j]=1
        with torch.no_grad():pred=model(x.to(device)).cpu()[0,0]*scale+mean
        for r in rows:
            if r['point_id'] in ids:continue
            # Inverse distance squared; only the same 20 observed points are used.
            weights=[1/((float(r['x_m'])-float(a['x_m']))**2+
                        (float(r['y_m'])-float(a['y_m']))**2) for a in inputs]
            idw=sum(w*float(a['rssi_median_dbm']) for w,a in zip(weights,inputs))/sum(weights)
            unet=pred[int(r['row'])-1,int(r['column'])-1].item()
            truth=float(r['rssi_median_dbm'])
            results.append(dict(trial=trial,point_id=r['point_id'],actual_dbm=truth,
                                unet_dbm=unet,idw_dbm=idw,unet_error_db=unet-truth,idw_error_db=idw-truth))
    metrics=[]
    for method in ('unet','idw'):
        errs=[r[method+'_error_db'] for r in results]
        metrics.append(dict(method=method,mae_db=sum(abs(e) for e in errs)/len(errs),
                            rmse_db=math.sqrt(sum(e*e for e in errs)/len(errs))))
    assert abs(metrics[0]['rmse_db']-ckpt['validation_rmse_db'])<1e-4
    for name,data in [('validation_comparison.csv',metrics),('validation_predictions.csv',results)]:
        with (run/name).open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=list(data[0]));writer.writeheader();writer.writerows(data)
    print('P64 validation only | 20 input points | 10 saved selections')
    print('Method       MAE (dB)    RMSE (dB)')
    for m in metrics:print(f'{m["method"]:10s} {m["mae_db"]:9.3f} {m["rmse_db"]:12.3f}')
    winner=min(metrics,key=lambda m:m['rmse_db'])['method']
    print('Lower validation RMSE:',winner)
    print('Repeated selections reuse P64 points; they are not independent deployments.')
    print('COMPARISON COMPLETE | Test scenes were not evaluated.')


if __name__=='__main__':
    main()
