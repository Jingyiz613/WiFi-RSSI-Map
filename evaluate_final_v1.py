"""Frozen v1 final evaluation. Never trains or selects checkpoints on test data."""
from pathlib import Path
import csv,json,random,math,hashlib,argparse
import torch
from torch import nn
from smoke_train import SmallUNet
from train_autoencoder import CompletionAutoencoder


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--compare-v2',action='store_true')
    args=parser.parse_args()
    root=Path(__file__).resolve().parent
    out=root/('output/reused_test_v1_v2' if args.compare_v2 else 'output/final_test_v1')
    if (out/'summary.json').exists():
        print((out/'summary.json').read_text());return
    out.mkdir(parents=True,exist_ok=True)
    paths={
      'unet':root/'output/unet_runs/20261021_134356_519488/best_model.pt',
      'residual_unet':root/'output/residual_runs/20261021_134850_642128/best_model.pt',
      'autoencoder':root/'output/autoencoder_runs/20261021_135427_799438/best_model.pt'}
    if args.compare_v2:
        paths={'residual_v1':paths['residual_unet'],
               'residual_v2':root/'output/residual_v2_runs/20261021_154833_150288/best_model.pt'}
    with (root/'output/training_tables_v1/training_points.csv').open(encoding='utf-8-sig',newline='') as f:
        rows=[r for r in csv.DictReader(f) if r['split']=='test']
    assert {r['ap_point'] for r in rows}=={'P49','P26'}
    choices={}
    for ap in ('P49','P26'):
        rng=random.Random(7400+int(ap[1:]))
        ids=[r['point_id'] for r in rows if r['ap_point']==ap]
        choices[ap]=[rng.sample(ids,20) for _ in range(10)]
    if args.compare_v2:
        original_plan=json.loads((root/'output/final_test_v1/frozen_plan.json').read_text())
        assert choices==original_plan['fixed_inputs']
        choices=original_plan['fixed_inputs']
    plan={'input_points':20,'selections_per_scene':10,'power_idw':2,
          'checkpoints':{k:{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for k,p in paths.items()},
          'fixed_inputs':choices,'new_P74_used':args.compare_v2,
          'evaluation_type':'reused_test_comparison_limited_independence' if args.compare_v2 else 'v1_test',
          'training_performed':False}
    (out/'frozen_plan.json').write_text(json.dumps(plan,indent=2),encoding='utf-8')
    device=torch.device('cuda' if torch.cuda.is_available() else 'cpu');torch.set_num_threads(2)
    models={}
    for name,path in paths.items():
        saved=torch.load(path,map_location=device,weights_only=True)
        model=CompletionAutoencoder() if name=='autoencoder' else SmallUNet()
        if name.startswith('residual'):model.encoder[0]=nn.Conv2d(4,16,3,padding=1)
        model.load_state_dict(saved['model_state_dict']);model.to(device).eval()
        models[name]=(model,saved['config'])
    predictions=[]
    for ap in ('P49','P26'):
        sr=[r for r in rows if r['ap_point']==ap];lookup={r['point_id']:r for r in sr}
        for trial,ids in enumerate(choices[ap],1):
            inputs=[lookup[i] for i in ids]
            base=torch.zeros(7,9)
            for i in range(7):
                for j in range(9):
                    weights=[1/max(((j+.5)*8.23/9-float(a['x_m']))**2+((i+.5)*6.95/7-float(a['y_m']))**2,1e-12) for a in inputs]
                    base[i,j]=sum(w*float(a['rssi_median_dbm']) for w,a in zip(weights,inputs))/sum(weights)
            maps={'idw':base}
            for name,(model,cfg) in models.items():
                mean,scale=cfg['mean_dbm'],cfg['scale_db']
                x=torch.zeros(1,4 if name.startswith('residual') else 3,7,9);x[:,2]=1
                for a in inputs:
                    i,j=int(a['row'])-1,int(a['column'])-1
                    x[0,0,i,j]=(float(a['rssi_median_dbm'])-mean)/scale;x[0,1,i,j]=1
                if name.startswith('residual'):x[0,3]=(base-mean)/scale
                with torch.no_grad():p=model(x.to(device)).cpu()[0,0]
                maps[name]=base+p*scale if name.startswith('residual') else p*scale+mean
            for r in sr:
                if r['point_id'] in ids:continue
                i,j=int(r['row'])-1,int(r['column'])-1;truth=float(r['rssi_median_dbm'])
                for name,p in maps.items():
                    value=p[i,j].item()
                    predictions.append(dict(ap=ap,trial=trial,point_id=r['point_id'],method=name,
                                            actual_dbm=truth,predicted_dbm=value,error_db=value-truth))
    metrics=[]
    for ap in ('P49','P26','combined'):
        for method in ['idw']+list(paths):
            e=[r['error_db'] for r in predictions if r['method']==method and (ap=='combined' or r['ap']==ap)]
            metrics.append(dict(scene=ap,method=method,mae_db=sum(abs(v) for v in e)/len(e),
                                rmse_db=math.sqrt(sum(v*v for v in e)/len(e)),scored_predictions=len(e)))
    for fn,data in [('metrics.csv',metrics),('predictions.csv',predictions)]:
        with (out/fn).open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(data[0]));w.writeheader();w.writerows(data)
    if args.compare_v2:
        previous_metrics=json.loads((root/'output/final_test_v1/summary.json').read_text())['metrics']
        for metric in metrics:
            if metric['method'] not in ('idw','residual_v1'):continue
            old=next(m for m in previous_metrics if m['scene']==metric['scene'] and m['method']==('residual_unet' if metric['method']=='residual_v1' else 'idw'))
            assert abs(old['rmse_db']-metric['rmse_db'])<1e-5
    summary={'metrics':metrics,'training_performed':False,'P74_in_models':args.compare_v2,
             'evaluation_type':plan['evaluation_type'],
             'limitations':['Only two deployments in the same room.','Repeated input selections are not independent deployments.',
                            'Test acquisition method differs from training.','Any changes based on this result require a fresh test for independent assessment.']}
    (out/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    lines=['# 两版残差模型重复测试对照' if args.compare_v2 else '# v1 最终测试','',
           '本次是重复使用 P49/P26 的固定对照，结论独立性有限，不是全新的独立测试。' if args.compare_v2 else '第一版冻结模型评分。',
           '每场景10组固定20点输入，仅在其余42个实测点计算误差。',
           '','|场景|方法|MAE dB|RMSE dB|','|---|---|---:|---:|']
    for r in metrics:lines.append(f'|{r["scene"]}|{r["method"]}|{r["mae_db"]:.3f}|{r["rmse_db"]:.3f}|')
    lines+=['','只有同一房间的两个测试部署；重复抽点不是独立场景。采样方式与训练集不同，因此不能把误差差异单独归因于路由器位置。',
            '本次结果查看后，P49/P26不得再用于调参后声称全新独立测试；后续若据此调整需新增未见部署作最终评估。',
            '第二版加入 P74 训练，第一版未加入；第二版沿用训练设置及 P64 验证选权重，本脚本仅评分，不训练或选择权重。' if args.compare_v2 else 'P74未加入第一版模型。',
            '归一化参数来自第一版训练数据并固定，两版共享。模型、输入点选择和文件哈希见 frozen_plan.json。',
            '不能仅凭一次加数据试验证明 P74 必然带来提升；不同训练批次也影响优化轨迹。当前报告不保证其他房间表现。']
    (out/'REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    for r in metrics:print(r,flush=True)


if __name__=='__main__':main()
