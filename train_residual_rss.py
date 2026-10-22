"""Input-only IDW plus learned residual; validation selects early stopping."""
from pathlib import Path
import csv,json,random,math,argparse,hashlib
from datetime import datetime
import numpy as np
import torch
from torch import nn
from smoke_train import SmallUNet


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--include-p74',action='store_true')
    args=parser.parse_args()
    root=Path(__file__).resolve().parent
    previous=root/'output/unet_runs/20261021_134356_519488'
    config=json.loads((previous/'config.json').read_text())
    selections=json.loads((previous/'validation_inputs.json').read_text())
    random.seed(2026);np.random.seed(2026);torch.manual_seed(2026);torch.set_num_threads(2)
    device=torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    with (root/'output/training_tables_v1/training_points.csv').open(encoding='utf-8-sig',newline='') as f:
        rows=[r for r in csv.DictReader(f) if r['split'] in ('train','validation')]
    additional_source=None
    if args.include_p74:
        additional_source=root/'output/rss_archive/AP_P74_20261021_140830/WiFi_RSS_P74_20261021/rss_summary_20261021_140830.csv'
        with additional_source.open(encoding='utf-8-sig',newline='') as f:
            extra=list(csv.DictReader(f))
        assert len(extra)==62 and {r['point_id'] for r in extra}=={f'P{i}{j}' for i in range(1,8) for j in range(1,10)}-{'P74'}
        assert all(int(r['valid_samples'])==10 and math.isfinite(float(r['rssi_median_dbm'])) for r in extra)
        rows.extend({**r,'ap_point':'P74','split':'train'} for r in extra)
        config['train_scenes']=config['train_scenes']+['P74']
    scenes={ap:[r for r in rows if r['ap_point']==ap] for ap in config['train_scenes']+['P64']}
    mean,scale=config['mean_dbm'],config['scale_db']
    coords=np.array([[(c+.5)*8.23/9,(r+.5)*6.95/7] for r in range(7) for c in range(9)])
    def sample(ap,ids=None):
        sr=scenes[ap];lookup={r['point_id']:r for r in sr}
        if ids is None:ids=random.sample(list(lookup),20)
        observed=[lookup[i] for i in ids]
        xy=np.array([[float(r['x_m']),float(r['y_m'])] for r in observed])
        values=np.array([float(r['rssi_median_dbm']) for r in observed])
        distance=((coords[:,None,:]-xy[None,:,:])**2).sum(2)
        weights=1/np.maximum(distance,1e-12)
        base=((weights@values)/weights.sum(1)).reshape(7,9)
        x=np.zeros((4,7,9),dtype=np.float32);x[2]=1;x[3]=(base-mean)/scale
        y=np.zeros((1,7,9),dtype=np.float32);mask=np.zeros_like(y)
        for r in sr:
            i,j=int(r['row'])-1,int(r['column'])-1
            y[0,i,j]=(float(r['rssi_median_dbm'])-mean)/scale
            if r['point_id'] in ids:x[0,i,j]=y[0,i,j];x[1,i,j]=1
            else:mask[0,i,j]=1
        return x,y,mask
    def batch(items):return [torch.from_numpy(np.stack([a[k] for a in items])).to(device) for k in range(3)]
    vx,vy,vm=batch([sample('P64',ids) for ids in selections])
    model=SmallUNet();model.encoder[0]=nn.Conv2d(4,16,3,padding=1)
    # Start exactly at IDW; subsequent gradients learn its correction.
    nn.init.zeros_(model.output.weight);nn.init.zeros_(model.output.bias)
    model=model.to(device)
    opt=torch.optim.AdamW(model.parameters(),lr=.0003,weight_decay=.01)
    out=root/('output/residual_v2_runs' if args.include_p74 else 'output/residual_runs')/datetime.now().strftime('%Y%m%d_%H%M%S_%f');out.mkdir(parents=True)
    run_config={**config,'model':'IDW_residual_SmallUNet','learning_rate':.0003,'weight_decay':.01,
                'residual_penalty':.05,'max_epochs':250,'patience':40,'input_channels':config['input_channels']+['input_only_idw'],
                'validation_inputs_source':str(previous/'validation_inputs.json')}
    run_config.update(version='v2_P74_added' if args.include_p74 else 'v1',
        normalization_policy='Frozen v1 training-only normalization for controlled comparison',
        additional_source=str(additional_source) if additional_source else None,
        additional_source_sha256=hashlib.sha256(additional_source.read_bytes()).hexdigest() if additional_source else None,
        training_points=sum(r['split']=='train' for r in rows),
        training_scene_count=len(config['train_scenes']),steps_per_epoch=10,test_evaluated=False)
    (out/'config.json').write_text(json.dumps(run_config,indent=2),encoding='utf-8')
    (out/'validation_inputs.json').write_text(json.dumps(selections,indent=2),encoding='utf-8')
    def evaluate():
        model.eval()
        with torch.no_grad():
            error=vx[:,3:4]+model(vx)-vy
            mse=(error.square()*vm).sum()/vm.sum()
            mae=(error.abs()*vm).sum()/vm.sum()
        return mse.item(),mae.item()*scale
    baseline,base_mae=evaluate();best=baseline;best_epoch=0
    torch.save({'model_state_dict':model.state_dict(),'config':run_config,'epoch':0},out/'best_model.pt')
    print('Output:',out,flush=True);print(f'IDW validation RMSE: {baseline**.5*scale:.3f} dB',flush=True)
    with (out/'history.csv').open('w',newline='') as f:
        w=csv.writer(f);w.writerow(['epoch','train_rmse_db','validation_rmse_db','validation_mae_db'])
        for epoch in range(1,251):
            model.train();total=0
            for _ in range(10):
                x,y,m=batch([sample(ap) for ap in config['train_scenes']])
                residual=model(x);err=x[:,3:4]+residual-y
                mse=(err.square()*m).sum()/m.sum()
                loss=mse+.05*(residual.square()*m).sum()/m.sum()
                if not torch.isfinite(loss):raise RuntimeError('Non-finite loss')
                opt.zero_grad();loss.backward();opt.step();total+=mse.item()
            val,mae=evaluate();w.writerow([epoch,(total/10)**.5*scale,val**.5*scale,mae]);f.flush()
            if val<best:
                best=val;best_epoch=epoch
                torch.save({'model_state_dict':model.state_dict(),'config':run_config,'epoch':epoch},out/'best_model.pt')
            if epoch==1 or epoch%10==0:print(f'Epoch {epoch}: validation {val**.5*scale:.3f} | best {best**.5*scale:.3f} dB',flush=True)
            if epoch-best_epoch>=40:break
    saved=torch.load(out/'best_model.pt',map_location=device,weights_only=True)
    model.load_state_dict(saved['model_state_dict']);restored,mae=evaluate()
    assert abs(restored-best)<1e-5
    summary={'best_epoch':best_epoch,'epochs_completed':epoch,'idw_rmse_db':baseline**.5*scale,
             'residual_rmse_db':restored**.5*scale,'residual_mae_db':mae,'idw_mae_db':base_mae,
             'checkpoint_reload_verified':True,'test_evaluated':False,
             'note':'P64 reused for model selection. Improvement here is not an independent test result.'}
    summary.update(version=run_config['version'],train_scenes=config['train_scenes'],training_points=run_config['training_points'])
    if args.include_p74:
        first=json.loads((root/'output/residual_runs/20261021_134850_642128/summary.json').read_text())
        summary['v1_residual_rmse_db']=first['residual_rmse_db']
        summary['rmse_change_v2_minus_v1_db']=summary['residual_rmse_db']-first['residual_rmse_db']
        (out/'REPORT.md').write_text(f'''# 第二版：增加 P74

训练场景：{', '.join(config['train_scenes'])}，共 {run_config['training_points']} 个实测点。
验证：P64，保持第一版相同的10组固定20点输入。P49/P26未用于本次评分或训练。
模型从头训练，初始化规则、学习率、正则项、归一化参数、每轮10步与早停规则沿用第一版；每批场景由6个增加到7个。

- 第一版残差模型 RMSE：{first['residual_rmse_db']:.6f} dB。
- 第二版残差模型 RMSE：{summary['residual_rmse_db']:.6f} dB。
- 距离插值 RMSE：{baseline**.5*scale:.6f} dB。
- 最佳轮次：{best_epoch}，实际训练 {epoch} 轮，模型重载核验通过。

这是单次随机种子的验证比较，P64已反复用于模型选择，不是新的独立测试。原始档案与第一版模型未修改。
''',encoding='utf-8')
    (out/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2),flush=True)


if __name__=='__main__':main()
