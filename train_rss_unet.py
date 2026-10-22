"""Train on six deployments; select checkpoint on P64 only. Never reads test targets."""
from pathlib import Path
import argparse
import csv
import hashlib
import json
import random
from datetime import datetime
import torch
from smoke_train import SmallUNet


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--epochs',type=int,default=300)
    parser.add_argument('--steps-per-epoch',type=int,default=10)
    args=parser.parse_args()
    if args.epochs<1 or args.steps_per_epoch<1:raise ValueError('Positive training counts required')
    root=Path(__file__).resolve().parent
    source=root/'output/training_tables_v1/training_points.csv'
    random.seed(2026);torch.manual_seed(2026);torch.set_num_threads(2)
    device=torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    with source.open(encoding='utf-8-sig',newline='') as f:
        rows=[r for r in csv.DictReader(f) if r['split'] in ('train','validation')]
    training=[r for r in rows if r['split']=='train']
    assert {r['ap_point'] for r in training}=={'P22','P68','P44','P28','P62','P16'}
    assert {r['ap_point'] for r in rows if r['split']=='validation'}=={'P64'}
    vals=torch.tensor([float(r['rssi_median_dbm']) for r in training])
    mean=vals.mean().item();scale=vals.std().clamp_min(1).item()
    scenes={}
    for ap in sorted({r['ap_point'] for r in rows}):
        sr=[r for r in rows if r['ap_point']==ap]
        y=torch.zeros(1,1,7,9);valid=torch.zeros_like(y)
        for r in sr:
            i,j=int(r['row'])-1,int(r['column'])-1
            y[0,0,i,j]=(float(r['rssi_median_dbm'])-mean)/scale
            valid[0,0,i,j]=1
        scenes[ap]=(y,valid,sr)
    def sample(ap,generator=None):
        y,valid,sr=scenes[ap]
        indices=torch.randperm(len(sr),generator=generator)[:20].tolist()
        observed=torch.zeros_like(valid)
        for k in indices:
            r=sr[k];observed[0,0,int(r['row'])-1,int(r['column'])-1]=1
        x=torch.cat([y*observed,observed,torch.ones_like(y)],dim=1)
        return x,y,valid-observed,[sr[k]['point_id'] for k in indices]
    out=root/'output/unet_runs'/datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    out.mkdir(parents=True,exist_ok=False)
    # Save fixed validation selections for fair repeatable comparisons.
    gen=torch.Generator().manual_seed(431)
    fixed=[sample('P64',gen) for _ in range(10)]
    vx=torch.cat([s[0] for s in fixed]).to(device)
    vy=torch.cat([s[1] for s in fixed]).to(device)
    vm=torch.cat([s[2] for s in fixed]).to(device)
    (out/'validation_inputs.json').write_text(json.dumps([s[3] for s in fixed],indent=2),encoding='utf-8')
    config={'model':'SmallUNet','mean_dbm':mean,'scale_db':scale,'rows':7,'columns':9,
            'input_channels':['normalized_sparse_rssi','observed_mask','room_mask'],
            'input_points':20,'train_scenes':sorted({r['ap_point'] for r in training}),
            'validation_scene':'P64','seed':2026,'epochs':args.epochs,
            'steps_per_epoch':args.steps_per_epoch,'learning_rate':.001,
            'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
            'torch_version':str(torch.__version__),'device':str(device),
            'test_evaluated':False}
    (out/'config.json').write_text(json.dumps(config,indent=2),encoding='utf-8')
    model=SmallUNet().to(device)
    optimizer=torch.optim.Adam(model.parameters(),lr=.001)
    def evaluate():
        model.eval()
        with torch.no_grad():
            errors=model(vx)-vy
            mse=((errors.square()*vm).sum()/vm.sum()).item()
            mae=((errors.abs()*vm).sum()/vm.sum()).item()*scale
        return mse,mae
    baseline,_=evaluate()
    print(f'Device: {device}',flush=True)
    print('Training: 6 scenes | Validation: P64 | Test: NOT evaluated',flush=True)
    print('Output: '+str(out),flush=True)
    print(f'Before training - validation RMSE: {baseline**.5*scale:.3f} dB',flush=True)
    best=float('inf');best_epoch=0
    with (out/'history.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.writer(f);writer.writerow(['epoch','train_rmse_db','validation_rmse_db','validation_mae_db'])
        for epoch in range(1,args.epochs+1):
            model.train();total=0
            for _ in range(args.steps_per_epoch):
                batch=[sample(ap) for ap in config['train_scenes']]
                x=torch.cat([s[0] for s in batch]).to(device)
                y=torch.cat([s[1] for s in batch]).to(device)
                mask=torch.cat([s[2] for s in batch]).to(device)
                optimizer.zero_grad();err=model(x)-y
                loss=(err.square()*mask).sum()/mask.sum()
                if not torch.isfinite(loss):raise RuntimeError('Non-finite loss')
                loss.backward();optimizer.step();total+=loss.item()
            mse,mae=evaluate()
            writer.writerow([epoch,(total/args.steps_per_epoch)**.5*scale,mse**.5*scale,mae]);f.flush()
            if mse<best:
                best=mse;best_epoch=epoch
                torch.save({'model_state_dict':model.state_dict(),'config':config,'epoch':epoch,
                            'validation_rmse_db':mse**.5*scale},out/'best_model.pt')
            if epoch==1 or epoch%10==0 or epoch==args.epochs:
                print(f'Epoch {epoch}/{args.epochs} | validation RMSE {mse**.5*scale:.3f} dB | best {best**.5*scale:.3f} dB',flush=True)
    # Confirm the saved model can be reloaded and reproduces its recorded score.
    checkpoint=torch.load(out/'best_model.pt',map_location=device,weights_only=True)
    model.load_state_dict(checkpoint['model_state_dict'])
    restored,_=evaluate()
    assert abs(restored**.5*scale-checkpoint['validation_rmse_db'])<1e-4
    summary={'best_epoch':best_epoch,'validation_rmse_db':best**.5*scale,
             'before_training_validation_rmse_db':baseline**.5*scale,
             'checkpoint_reload_verified':True,'test_evaluated':False}
    (out/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print('TRAINING COMPLETE',flush=True)
    print(f'Best epoch: {best_epoch} | validation RMSE: {best**.5*scale:.3f} dB',flush=True)
    print('Saved model: '+str(out/'best_model.pt'),flush=True)


if __name__=='__main__':
    main()
