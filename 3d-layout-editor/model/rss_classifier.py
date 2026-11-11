"""Five-class RSSI completion. Thresholds are project-relative, not service-quality limits."""
from pathlib import Path
import csv,json,random,argparse,math,hashlib
from datetime import datetime
import numpy as np
import torch
from torch import nn
from torch.nn import functional as F
from smoke_train import SmallUNet

ROOT=Path(__file__).resolve().parent
THRESHOLDS=[-40.,-35.,-30.,-25.]
LABELS=['弱','较弱','中','较强','强']
TRAIN=['P16','P22','P28','P44','P62','P68','P74']


class Classifier(SmallUNet):
    def __init__(self):
        super().__init__()
        self.encoder[0]=nn.Conv2d(4,16,3,padding=1)
        self.output=nn.Conv2d(16,5,1)


def read(path):
    with Path(path).open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))


def label(value):return int(np.searchsorted(THRESHOLDS,value,side='right'))


def features(inputs,mean,scale):
    """Only supplied coordinates and RSSI enter feature construction."""
    coords=np.array([[(c+.5)*8.23/9,(r+.5)*6.95/7] for r in range(7) for c in range(9)])
    xy=np.array([[float(r['x_m']),float(r['y_m'])] for r in inputs])
    values=np.array([float(r['rssi_median_dbm']) for r in inputs])
    w=1/np.maximum(((coords[:,None]-xy[None])**2).sum(2),1e-12)
    base=((w@values)/w.sum(1)).reshape(7,9)
    x=np.zeros((4,7,9),np.float32);x[2]=1;x[3]=(base-mean)/scale
    for r in inputs:
        i,j=int(r['row'])-1,int(r['column'])-1
        x[0,i,j]=(float(r['rssi_median_dbm'])-mean)/scale;x[1,i,j]=1
    return x,base


def metrics(truth,pred):
    cm=np.zeros((5,5),int)
    for a,b in zip(truth,pred):cm[int(a),int(b)]+=1
    tp=np.diag(cm);support=cm.sum(1);predsupport=cm.sum(0)
    f1=np.divide(2*tp,support+predsupport,out=np.zeros(5,float),where=support+predsupport>0)
    recall=np.divide(tp,support,out=np.zeros(5,float),where=support>0)
    delta=np.abs(np.asarray(truth)-np.asarray(pred))
    return dict(accuracy=float(np.mean(delta==0)),macro_f1_5_classes=float(f1.mean()),
        within_one_grade_accuracy=float(np.mean(delta<=1)),mean_grade_error=float(delta.mean()),
        severe_error_rate=float(np.mean(delta>=2)),confusion_matrix=cm.tolist(),
        support=support.tolist(),recall_per_class=recall.tolist(),f1_per_class=f1.tolist(),
        note='Macro-F1 includes all five fixed classes; absent classes score zero.')


def train(quantile_grades=False):
    random.seed(2026);np.random.seed(2026);torch.manual_seed(2026);torch.set_num_threads(2)
    original=ROOT/'output/training_tables_v1/training_points.csv'
    p74=ROOT/'output/rss_archive/AP_P74_20261021_140830/WiFi_RSS_P74_20261021/rss_summary_20261021_140830.csv'
    rows=[r for r in read(original) if r['split'] in ('train','validation')]
    rows.extend({**r,'ap_point':'P74','split':'train'} for r in read(p74))
    scenes={ap:[r for r in rows if r['ap_point']==ap] for ap in TRAIN+['P64']}
    tr=[r for r in rows if r['split']=='train'];assert len(tr)==433
    vals=np.array([float(r['rssi_median_dbm']) for r in tr]);mean=float(vals.mean());scale=float(vals.std(ddof=1))
    if quantile_grades:
        # Fixed training-only 20/40/60/80 percentiles, rounded to nearest 0.5 dB.
        THRESHOLDS[:]=[float(v) for v in np.round(np.quantile(vals,[.2,.4,.6,.8])*2)/2]
        assert all(a<b for a,b in zip(THRESHOLDS,THRESHOLDS[1:]))
    counts=np.bincount([label(v) for v in vals],minlength=5)
    weights=1/np.sqrt(counts.astype(float));weights/=weights.mean()
    selections=json.loads((ROOT/'output/unet_runs/20261021_134356_519488/validation_inputs.json').read_text())
    out=ROOT/'output/classification_runs'/datetime.now().strftime('%Y%m%d_%H%M%S_%f');out.mkdir(parents=True)
    cfg=dict(model='FiveClassUNet',labels=LABELS,thresholds_dbm=list(THRESHOLDS),
        grade_policy='training_only_quintiles_rounded_0.5db' if quantile_grades else 'fixed_project_thresholds_v1',
        training_quantiles_dbm=np.quantile(vals,[.2,.4,.6,.8]).tolist() if quantile_grades else None,
        interpretation='Project-relative signal strength; not universal Wi-Fi quality categories.',
        mean_dbm=mean,scale_db=scale,train_scenes=TRAIN,validation_scene='P64',
        training_points=433,training_class_counts=counts.tolist(),class_weights=weights.tolist(),
        input_channels=['normalized_sparse_rssi','observed_mask','room_mask','normalized_input_only_idw'],
        output='5 categorical logits, softmax probabilities, argmax class; no RSSI regression output',
        epochs_max=250,patience=40,steps_per_epoch=10,learning_rate=.0003,weight_decay=.01,seed=2026,
        input_points=20,rows=7,columns=9,room_width_m=8.23,room_height_m=6.95,test_evaluated=False,
        source_sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [original,p74]})
    (out/'config.json').write_text(json.dumps(cfg,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'validation_inputs.json').write_text(json.dumps(selections,indent=2),encoding='utf-8')
    def sample(ap,ids=None):
        sr=scenes[ap]
        if ids is None:ids=random.sample([r['point_id'] for r in sr],20)
        observed=[r for r in sr if r['point_id'] in ids]
        x,base=features(observed,mean,scale);y=np.zeros((7,9),np.int64);mask=np.zeros((7,9),bool)
        for r in sr:
            i,j=int(r['row'])-1,int(r['column'])-1
            y[i,j]=label(float(r['rssi_median_dbm']));mask[i,j]=r['point_id'] not in ids
        return x,y,mask,base
    device=torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    def batch(items):return [torch.from_numpy(np.stack([s[k] for s in items])).to(device) for k in range(3)]
    fixed=[sample('P64',ids) for ids in selections];vx,vy,vm=batch(fixed)
    truth=vy[vm].cpu().numpy()
    basepred=np.stack([np.searchsorted(THRESHOLDS,s[3],side='right') for s in fixed])[vm.cpu().numpy()]
    baseline=metrics(truth,basepred)
    model=Classifier().to(device);opt=torch.optim.AdamW(model.parameters(),lr=.0003,weight_decay=.01)
    class_weights=torch.tensor(weights,dtype=torch.float32,device=device)
    best=-1.;best_epoch=0
    def evaluate():
        model.eval()
        with torch.no_grad():
            logits=model(vx);pred=logits.argmax(1)
        return metrics(truth,pred[vm].cpu().numpy())
    print('Device:',device,'| Training class counts:',counts.tolist(),flush=True)
    print('Output:',out,flush=True)
    with (out/'history.csv').open('w',encoding='utf-8',newline='') as f:
        w=csv.writer(f);w.writerow(['epoch','train_cross_entropy','validation_accuracy','validation_macro_f1','validation_mean_grade_error'])
        for epoch in range(1,251):
            model.train();total=0.
            for _ in range(10):
                x,y,m=batch([sample(ap) for ap in TRAIN]);logits=model(x)
                loss=F.cross_entropy(logits.permute(0,2,3,1)[m],y[m],weight=class_weights)
                assert torch.isfinite(loss)
                opt.zero_grad();loss.backward();opt.step();total+=loss.item()
            val=evaluate();w.writerow([epoch,total/10,val['accuracy'],val['macro_f1_5_classes'],val['mean_grade_error']]);f.flush()
            if val['macro_f1_5_classes']>best+1e-8:
                best=val['macro_f1_5_classes'];best_epoch=epoch
                torch.save(dict(model_state_dict=model.state_dict(),config=cfg,epoch=epoch,validation_metrics=val),out/'best_model.pt')
            if epoch==1 or epoch%10==0:print(f'Epoch {epoch}: validation accuracy {val["accuracy"]:.3f} | macro-F1 {val["macro_f1_5_classes"]:.3f}',flush=True)
            if epoch-best_epoch>=40:break
    saved=torch.load(out/'best_model.pt',map_location=device,weights_only=True)
    model.load_state_dict(saved['model_state_dict']);val=evaluate()
    assert abs(val['macro_f1_5_classes']-best)<1e-8
    summary=dict(best_epoch=best_epoch,epochs_completed=epoch,classification_model=val,idw_binned_baseline=baseline,
        test_evaluated=False,checkpoint_reload_verified=True,interpretation='P64 reused for model selection. No independent new test.')
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    with torch.no_grad():probs=model(vx).softmax(1).cpu().numpy()
    # Saved first fixed selection is a predetermined display example, not cherry-picked.
    np.savez(out/'validation_display.npz',probabilities=probs[0],truth=fixed[0][1],hidden_mask=fixed[0][2],
             input_mask=fixed[0][0][1],baseline=np.searchsorted(THRESHOLDS,fixed[0][3],side='right'))
    lines=['# 五档分类实验','',
        '分档为本项目相对信号强弱，非通用Wi-Fi可用性或网速标准。根据训练数据范围固定，没有按测试成绩选阈值。',
        '','|类别|范围 dBm|七组训练点数|','|---|---|---:|']
    intervals=[f'RSSI < {THRESHOLDS[0]:g}']+[f'{a:g} ≤ RSSI < {b:g}' for a,b in zip(THRESHOLDS,THRESHOLDS[1:])]+[f'RSSI ≥ {THRESHOLDS[-1]:g}']
    for name,interval,n in zip(LABELS,intervals,counts):lines.append(f'|{name}|{interval}|{n}|')
    if quantile_grades:
        lines+=['','分档规则：仅使用七组训练场景的20/40/60/80百分位点，取最近0.5 dB作为边界；不使用验证或测试数据决定阈值。相同RSSI不拆分，所以类别数不会精确相等。',
            '任务定义已改变，新旧模型准确率不可直接比较。旧版弱档不是没有数据，而是P64弱档召回率为0。']
    lines+=['','网络直接输出五类概率和类别，没有RSSI回归输出；距离估算图仅为输入特征。损失为加权交叉熵，仅隐藏实测点参与，权重仅由训练类频数决定。',
        '7组训练：'+', '.join(TRAIN)+'；P64固定10组20点输入，隐藏42点。标准化仅来自训练数据。P49/P26未评分。',
        '','|方法|准确率|五类宏平均F1|相差不超过一档|平均错档数|','|---|---:|---:|---:|---:|']
    for name,m in [('五档分类网络',val),('距离估算后按同一阈值分档',baseline)]:lines.append(f'|{name}|{m["accuracy"]:.1%}|{m["macro_f1_5_classes"]:.3f}|{m["within_one_grade_accuracy"]:.1%}|{m["mean_grade_error"]:.3f}|')
    lines+=['',f'最佳第{best_epoch}轮，运行{epoch}轮。按验证集五类宏平均F1选权重。',
        '准确率只指预测类别正确，不等于RSSI数值更准确；五档任务与此前回归任务指标不能直接比较。',
        '重复选点不是独立场景；P64已反复用于模型选择，结果仅为验证，不能声称新独立测试或跨房间效果。空房间使距离估算有竞争力是合理假设，尚未单独验证因果。',
        '低于阈值到边界的细小变化可改变一档。置信概率未经过校准，不能视为真实正确率。原有回归实验未替换或删除。']
    (out/'REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False),flush=True)
    print('CLASSIFICATION TRAINING COMPLETE',flush=True)


def predict(args):
    saved=torch.load(args.model,map_location='cpu',weights_only=True);cfg=saved['config']
    THRESHOLDS[:]=cfg['thresholds_dbm']
    model=Classifier();model.load_state_dict(saved['model_state_dict']);model.eval()
    inputs=[];seen=set()
    for r in read(args.input):
        p=r['point_id'].strip().upper()
        if len(p)!=3 or p[0]!='P' or not p[1:].isdigit() or not 1<=int(p[1])<=7 or not 1<=int(p[2])<=9 or p in seen:raise ValueError('Invalid/duplicate point '+p)
        value=float(r['rssi_dbm'])
        if not math.isfinite(value):raise ValueError('Nonfinite value')
        seen.add(p);i,j=int(p[1]),int(p[2])
        inputs.append(dict(point_id=p,row=i,column=j,x_m=round((j-.5)*8.23/9,6),y_m=round((i-.5)*6.95/7,6),rssi_median_dbm=value))
    if not inputs:raise ValueError('No inputs')
    x,_=features(inputs,cfg['mean_dbm'],cfg['scale_db'])
    with torch.no_grad():probs=model(torch.from_numpy(x)[None]).softmax(1)[0].numpy()
    output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.writer(f);w.writerow(['point_id','predicted_grade','max_probability_uncalibrated','is_input']+['prob_'+s for s in LABELS])
        for i in range(7):
            for j in range(9):
                p=f'P{i+1}{j+1}';v=probs[:,i,j];w.writerow([p,LABELS[int(v.argmax())],float(v.max()),int(p in seen)]+v.tolist())
    print('Saved five-class predictions:',output)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--model');parser.add_argument('--input');parser.add_argument('--output',default='classification_prediction.csv')
    parser.add_argument('--quantile-grades',action='store_true',help='Use training-only quintiles rounded to 0.5 dB')
    args=parser.parse_args()
    if args.model:
        if not args.input:parser.error('--input required with --model')
        predict(args)
    else:train(quantile_grades=args.quantile_grades)
