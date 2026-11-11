"""Direct dense-grid classification, supervised only at native measured coordinates."""
from pathlib import Path
import csv,json,random,hashlib
from datetime import datetime
import numpy as np
import torch
from torch import nn
from torch.nn import functional as F
from rss_classifier import read,metrics

ROOT=Path(__file__).resolve().parent
TRAIN=['P16','P22','P28','P44','P62','P68','P74']
NX,NY=76,64
W,H=8.23,6.95


class Block(nn.Sequential):
    def __init__(self,a,b):
        super().__init__(nn.Conv2d(a,b,3,padding=1),nn.GroupNorm(4,b),nn.SiLU(),nn.Conv2d(b,b,3,padding=1),nn.GroupNorm(4,b),nn.SiLU())


class DenseClassifier(nn.Module):
    def __init__(self):
        super().__init__()
        self.e1=Block(6,12);self.e2=Block(12,24);self.mid=Block(24,48)
        self.d2=Block(72,24);self.d1=Block(36,12);self.head=nn.Conv2d(12,5,1)
    def forward(self,x):
        a=self.e1(x);b=self.e2(F.avg_pool2d(a,2));c=self.mid(F.avg_pool2d(b,2))
        d=self.d2(torch.cat([F.interpolate(c,size=b.shape[-2:],mode='bilinear',align_corners=False),b],1))
        return self.head(self.d1(torch.cat([F.interpolate(d,size=a.shape[-2:],mode='bilinear',align_corners=False),a],1)))


def build_input(observed,mean,scale):
    gx,gy=np.meshgrid((np.arange(NX)+.5)*W/NX,(np.arange(NY)+.5)*H/NY)
    q=np.stack([gx.ravel(),gy.ravel()],1)
    xy=np.array([[float(r['x_m']),float(r['y_m'])] for r in observed]);v=np.array([float(r['rssi_median_dbm']) for r in observed])
    dist=((q[:,None]-xy[None])**2).sum(2);weight=1/np.maximum(dist,1e-12)
    base=(weight@v)/weight.sum(1)
    x=np.zeros((6,NY,NX),np.float32);x[2]=1;x[3]=(base.reshape(NY,NX)-mean)/scale
    x[4]=np.sqrt(dist.min(1)).reshape(NY,NX)/np.hypot(W,H)
    x[5]=np.exp(-dist/(2*.6**2)).sum(1).reshape(NY,NX)/20
    for r in observed:
        i=min(NY-1,int(float(r['y_m'])/H*NY));j=min(NX-1,int(float(r['x_m'])/W*NX))
        x[0,i,j]=(float(r['rssi_median_dbm'])-mean)/scale;x[1,i,j]=1
    return x,base.reshape(NY,NX)


def main():
    random.seed(2026);torch.manual_seed(2026);torch.set_num_threads(2)
    prior=ROOT/'output/classification_runs/20261029_160910_039926'
    oldcfg=json.loads((prior/'config.json').read_text(encoding='utf-8'));thresholds=np.array(oldcfg['thresholds_dbm'])
    rows=[r for r in read(ROOT/'output/training_tables_v1/training_points.csv') if r['split'] in ('train','validation')]
    rows.extend({**r,'ap_point':'P74','split':'train'} for r in read(ROOT/'output/rss_archive/AP_P74_20261021_140830/WiFi_RSS_P74_20261021/rss_summary_20261021_140830.csv'))
    scenes={ap:[r for r in rows if r['ap_point']==ap] for ap in TRAIN+['P64']}
    mean,scale=oldcfg['mean_dbm'],oldcfg['scale_db']
    gx,gy=np.meshgrid((np.arange(NX)+.5)*W/NX,(np.arange(NY)+.5)*H/NY)
    q=np.stack([gx.ravel(),gy.ravel()],1)
    def sample(ap,ids=None):
        sr=scenes[ap]
        if ids is None:ids=random.sample([r['point_id'] for r in sr],20)
        observed=[r for r in sr if r['point_id'] in ids];hidden=[r for r in sr if r['point_id'] not in ids]
        x,base=build_input(observed,mean,scale)
        positions=np.array([[2*float(r['x_m'])/W-1,2*float(r['y_m'])/H-1] for r in hidden],np.float32)
        labels=np.searchsorted(thresholds,[float(r['rssi_median_dbm']) for r in hidden],side='right')
        # P62 has one fewer valid point: padded targets are ignored.
        coords=np.zeros((42,2),np.float32);coords[:len(hidden)]=positions
        targets=np.full(42,-100,np.int64);targets[:len(hidden)]=labels
        return x,coords,targets,base
    device=torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    def batch(items):return [torch.from_numpy(np.stack([s[k] for s in items])).to(device) for k in range(3)]
    selections=json.loads((prior/'validation_inputs.json').read_text())
    fixed=[sample('P64',ids) for ids in selections];vx,vc,vy=batch(fixed)
    def at_points(logits,coords):
        return F.grid_sample(logits,coords[:,:,None,:],align_corners=False,mode='bilinear',padding_mode='border').squeeze(-1).transpose(1,2)
    model=DenseClassifier().to(device);opt=torch.optim.AdamW(model.parameters(),lr=.0003,weight_decay=.01)
    weights=torch.tensor(oldcfg['class_weights'],device=device)
    out=ROOT/'output/dense_classification_runs'/datetime.now().strftime('%Y%m%d_%H%M%S_%f');out.mkdir(parents=True)
    cfg={**oldcfg,'model':'DenseFiveClassUNet','output_shape':[NY,NX],
         'input_channels':['sparse_rssi','observation_mask','room_mask','input_only_idw','nearest_observation_distance','observation_density'],
         'supervision':'Bilinear sampling of output logits at original measured coordinates; hidden measurements only.',
         'max_epochs':150,'epochs_max':150,'patience':25,'steps_per_epoch':6,'seed':2026,'smoothness_weight':0,
         'note':'Thresholds frozen from training-only quintiles of prior classification; no added pseudo-labels.'}
    (out/'config.json').write_text(json.dumps(cfg,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'validation_inputs.json').write_text(json.dumps(selections,indent=2),encoding='utf-8')
    def evaluate():
        model.eval()
        with torch.no_grad():pred=at_points(model(vx),vc).argmax(-1)
        return metrics(vy.flatten().cpu().numpy(),pred.flatten().cpu().numpy())
    best=-1;best_epoch=0
    print('DIRECT DENSE OUTPUT',NY,NX,'Device',device,'Output',out,flush=True)
    with (out/'history.csv').open('w',newline='') as f:
        writer=csv.writer(f);writer.writerow(['epoch','train_loss','validation_accuracy','validation_macro_f1'])
        for epoch in range(1,151):
            model.train();loss_total=0
            for _ in range(6):
                x,coord,y=batch([sample(ap) for ap in TRAIN]);logits=model(x)
                loss=F.cross_entropy(at_points(logits,coord).reshape(-1,5),y.reshape(-1),weight=weights,ignore_index=-100)
                assert torch.isfinite(loss)
                opt.zero_grad();loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),1);opt.step();loss_total+=loss.item()
            val=evaluate();writer.writerow([epoch,loss_total/6,val['accuracy'],val['macro_f1_5_classes']]);f.flush()
            if val['macro_f1_5_classes']>best+1e-8:
                best=val['macro_f1_5_classes'];best_epoch=epoch
                torch.save(dict(model_state_dict=model.state_dict(),config=cfg,epoch=epoch,validation_metrics=val),out/'best_model.pt')
            if epoch==1 or epoch%10==0:print(f'Epoch {epoch} accuracy {val["accuracy"]:.3f} macro-F1 {val["macro_f1_5_classes"]:.3f}',flush=True)
            if epoch-best_epoch>=25:break
    saved=torch.load(out/'best_model.pt',map_location=device,weights_only=True);model.load_state_dict(saved['model_state_dict'])
    val=evaluate();assert abs(val['macro_f1_5_classes']-best)<1e-8
    # Visual example is chosen in advance: first saved P64 selection.
    with torch.no_grad():prob=model(vx[:1]).softmax(1)[0].cpu().numpy()
    np.savez_compressed(out/'P64_direct_output.npz',probabilities=prob,classes=prob.argmax(0),input_ids=np.array(selections[0]))
    baseline_predictions=[]
    for ids in selections:
        sr=scenes['P64'];inp=[r for r in sr if r['point_id'] in ids];hid=[r for r in sr if r['point_id'] not in ids]
        xy=np.array([[float(r['x_m']),float(r['y_m'])] for r in inp]);v=np.array([float(r['rssi_median_dbm']) for r in inp])
        pts=np.array([[float(r['x_m']),float(r['y_m'])] for r in hid]);w=1/np.maximum(((pts[:,None]-xy[None])**2).sum(2),1e-12)
        baseline_predictions.extend(np.searchsorted(thresholds,(w@v)/w.sum(1),side='right').tolist())
    baseline=metrics(vy.flatten().cpu().numpy(),baseline_predictions)
    summary=dict(best_epoch=best_epoch,epochs_run=epoch,direct_output_shape=[NY,NX],validation_metrics=val,
        idw_validation_metrics=baseline,test_evaluated=False,checkpoint_reload_verified=True,
        warning='P64 reused for model selection; dense pixels lack dense ground truth.')
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False),flush=True)
    print('DENSE CLASSIFICATION COMPLETE',flush=True)


if __name__=='__main__':main()
