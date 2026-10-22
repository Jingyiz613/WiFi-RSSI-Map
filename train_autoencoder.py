"""Small convolutional completion autoencoder, with no skip connections."""
from pathlib import Path
import csv,json,random,math
from datetime import datetime
import numpy as np
import torch
from torch import nn
from torch.nn import functional as F


class CompletionAutoencoder(nn.Module):
    def __init__(self):
        super().__init__()
        self.encoder=nn.Sequential(nn.Conv2d(3,8,3,padding=1),nn.ReLU(),
            nn.Conv2d(8,16,3,stride=2,padding=1),nn.ReLU(),
            nn.Conv2d(16,16,3,padding=1),nn.ReLU())
        self.decoder=nn.Sequential(nn.Conv2d(16,8,3,padding=1),nn.ReLU(),nn.Conv2d(8,1,3,padding=1))
    def forward(self,x):
        z=self.encoder(x)
        return self.decoder(F.interpolate(z,size=x.shape[-2:],mode='bilinear',align_corners=False))


def main():
    root=Path(__file__).resolve().parent
    prior=root/'output/unet_runs/20261021_134356_519488'
    config=json.loads((prior/'config.json').read_text())
    choices=json.loads((prior/'validation_inputs.json').read_text())
    random.seed(2026);torch.manual_seed(2026);torch.set_num_threads(2)
    device=torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    with (root/'output/training_tables_v1/training_points.csv').open(encoding='utf-8-sig',newline='') as f:
        rows=[r for r in csv.DictReader(f) if r['split'] in ('train','validation')]
    train=[r for r in rows if r['split']=='train']
    assert {r['ap_point'] for r in train}==set(config['train_scenes'])
    mean,scale=config['mean_dbm'],config['scale_db']
    scenes={ap:[r for r in rows if r['ap_point']==ap] for ap in config['train_scenes']+['P64']}
    def sample(ap,ids=None):
        sr=scenes[ap]
        if ids is None:ids=random.sample([r['point_id'] for r in sr],20)
        x=np.zeros((3,7,9),np.float32);x[2]=1
        y=np.zeros((1,7,9),np.float32);mask=np.zeros_like(y)
        for r in sr:
            i,j=int(r['row'])-1,int(r['column'])-1
            y[0,i,j]=(float(r['rssi_median_dbm'])-mean)/scale
            if r['point_id'] in ids:x[0,i,j]=y[0,i,j];x[1,i,j]=1
            else:mask[0,i,j]=1
        return x,y,mask
    def batch(items):return [torch.from_numpy(np.stack([s[k] for s in items])).to(device) for k in range(3)]
    vx,vy,vm=batch([sample('P64',ids) for ids in choices])
    model=CompletionAutoencoder().to(device)
    opt=torch.optim.AdamW(model.parameters(),lr=.001,weight_decay=.01)
    out=root/'output/autoencoder_runs'/datetime.now().strftime('%Y%m%d_%H%M%S_%f');out.mkdir(parents=True)
    cfg={**config,'model':'CompletionAutoencoder','skip_connections':False,'parameters':sum(p.numel() for p in model.parameters()),
         'weight_decay':.01,'max_epochs':300,'patience':50,'validation_inputs_source':str(prior/'validation_inputs.json')}
    (out/'config.json').write_text(json.dumps(cfg,indent=2),encoding='utf-8')
    (out/'validation_inputs.json').write_text(json.dumps(choices,indent=2),encoding='utf-8')
    def evaluate():
        model.eval()
        with torch.no_grad():
            e=model(vx)-vy
            return ((e.square()*vm).sum()/vm.sum()).item(),((e.abs()*vm).sum()/vm.sum()).item()*scale
    baseline,_=evaluate();best=baseline;best_epoch=0
    torch.save({'model_state_dict':model.state_dict(),'config':cfg,'epoch':0},out/'best_model.pt')
    print('Device:',device,'| Parameters:',cfg['parameters'],flush=True)
    print('Same six training scenes and P64 fixed validation inputs. No test evaluation.',flush=True)
    print('Output:',out,flush=True)
    with (out/'history.csv').open('w',newline='') as f:
        w=csv.writer(f);w.writerow(['epoch','train_rmse_db','validation_rmse_db','validation_mae_db'])
        for epoch in range(1,301):
            model.train();total=0
            for _ in range(10):
                x,y,m=batch([sample(ap) for ap in config['train_scenes']])
                e=model(x)-y;loss=(e.square()*m).sum()/m.sum()
                if not torch.isfinite(loss):raise RuntimeError('Non-finite loss')
                opt.zero_grad();loss.backward();opt.step();total+=loss.item()
            val,mae=evaluate();w.writerow([epoch,(total/10)**.5*scale,val**.5*scale,mae]);f.flush()
            if val<best:
                best=val;best_epoch=epoch
                torch.save({'model_state_dict':model.state_dict(),'config':cfg,'epoch':epoch},out/'best_model.pt')
            if epoch==1 or epoch%10==0:print(f'Epoch {epoch} | validation RMSE {val**.5*scale:.3f} | best {best**.5*scale:.3f} dB',flush=True)
            if epoch-best_epoch>=50:break
    saved=torch.load(out/'best_model.pt',map_location=device,weights_only=True)
    restored=CompletionAutoencoder().to(device);restored.load_state_dict(saved['model_state_dict']);model=restored
    val,mae=evaluate();assert abs(val-best)<1e-5
    summary={'model':'CompletionAutoencoder','best_epoch':best_epoch,'epochs_completed':epoch,
             'validation_rmse_db':val**.5*scale,'validation_mae_db':mae,
             'initial_validation_rmse_db':baseline**.5*scale,'checkpoint_reload_verified':True,
             'test_evaluated':False,'note':'P64 is used repeatedly for model selection; not an independent final score.'}
    (out/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2),flush=True)
    # Store the full comparison as plain text; all values are actual saved validation results.
    old=json.loads((prior/'summary.json').read_text())
    residual_root=root/'output/residual_runs'
    residuals=sorted(residual_root.glob('*/summary.json'))
    residual=json.loads(residuals[-1].read_text()) if residuals else None
    report=['# 三种网络的 P64 验证比较','','使用相同六组训练场景、相同 P64 的10组固定20点输入，仅在隐藏实测点评分。单位 dB，越低越好。',
            '这是单次训练和反复使用验证集的探索结果，不代表独立最终测试；各网络优化设置不同，不能视作全面架构优劣结论。','',
            '|方法|RMSE|','|---|---:|',f'|卷积补全自编码器|{summary["validation_rmse_db"]:.4f}|',
            f'|小型 U-Net|{old["validation_rmse_db"]:.4f}|']
    if residual:report += [f'|插值引导残差 U-Net|{residual["residual_rmse_db"]:.4f}|',f'|纯距离插值 IDW|{residual["idw_rmse_db"]:.4f}|']
    (out/'COMPARISON.md').write_text('\n'.join(report)+'\n',encoding='utf-8')
    print('AUTOENCODER TRAINING COMPLETE',flush=True)


if __name__=='__main__':main()
