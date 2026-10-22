"""Portable inference for the four saved RSSI model checkpoints."""
from pathlib import Path
import argparse,csv,json
import torch
from torch import nn
from smoke_train import SmallUNet
from train_autoencoder import CompletionAutoencoder


def main():
    parser=argparse.ArgumentParser(description='Input CSV: point_id,rssi_dbm; fixed 7x9 room grid.')
    parser.add_argument('--model',required=True,help='Path to trusted best_model.pt')
    parser.add_argument('--input',required=True,help='CSV of measured points only')
    parser.add_argument('--output',default='prediction.csv')
    parser.add_argument('--device',choices=['cpu','cuda'],default='cpu')
    args=parser.parse_args()
    with Path(args.input).open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
    points={}
    for r in rows:
        p=r['point_id'].upper().strip()
        if len(p)!=3 or p[0]!='P' or not p[1:].isdigit() or not 1<=int(p[1])<=7 or not 1<=int(p[2])<=9:
            raise ValueError('Invalid grid point: '+p)
        value=float(r['rssi_dbm'])
        if not torch.isfinite(torch.tensor(value)):raise ValueError('Non-finite RSSI')
        if p in points:raise ValueError('Duplicate point: '+p)
        points[p]=value
    if not points:raise ValueError('At least one measured point is required')
    device=torch.device(args.device)
    saved=torch.load(args.model,map_location=device,weights_only=True);cfg=saved['config']
    name=cfg['model'];residual=name=='IDW_residual_SmallUNet'
    if name=='CompletionAutoencoder':model=CompletionAutoencoder()
    elif residual or name=='SmallUNet':
        model=SmallUNet()
        if residual:model.encoder[0]=nn.Conv2d(4,16,3,padding=1)
    else:raise ValueError('Unsupported model: '+name)
    model.load_state_dict(saved['model_state_dict']);model.to(device).eval()
    mean,scale=cfg['mean_dbm'],cfg['scale_db']
    x=torch.zeros(1,4 if residual else 3,7,9);x[:,2]=1
    for p,value in points.items():
        i,j=int(p[1])-1,int(p[2])-1
        x[0,0,i,j]=(value-mean)/scale;x[0,1,i,j]=1
    base=torch.zeros(7,9)
    if residual:
        for i in range(7):
            for j in range(9):
                # Match stored training coordinates (six decimal places).
                pairs=[]
                for p,v in points.items():
                    px=round((int(p[2])-.5)*8.23/9,6);py=round((int(p[1])-.5)*6.95/7,6)
                    d=((j+.5)*8.23/9-px)**2+((i+.5)*6.95/7-py)**2
                    pairs.append((1/max(d,1e-12),v))
                base[i,j]=sum(w*v for w,v in pairs)/sum(w for w,v in pairs)
        x[0,3]=(base-mean)/scale
    with torch.no_grad():prediction=model(x.to(device)).cpu()[0,0]*scale
    prediction+=base if residual else mean
    output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.writer(f);writer.writerow(['point_id','x_m','y_m','predicted_rssi_dbm','is_input','measured_rssi_dbm'])
        for i in range(7):
            for j in range(9):
                p=f'P{i+1}{j+1}'
                writer.writerow([p,round((j+.5)*8.23/9,6),round((i+.5)*6.95/7,6),prediction[i,j].item(),int(p in points),points.get(p,'')])
    print('Saved 63 grid predictions:',output)
    print('Room: 8.23m x 6.95m. Unmeasured predictions are not verified measurements.')


if __name__=='__main__':main()
