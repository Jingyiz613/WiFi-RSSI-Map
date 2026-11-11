"""Inference reads a checkpoint and observed points only; never reference maps."""
import argparse, csv
from pathlib import Path
import numpy as np
import torch
from models import CompletionNet, features, ROWS, COLS

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--checkpoint',type=Path,required=True)
    parser.add_argument('--points',type=Path,required=True,help='CSV columns: point_id,rssi_dbm')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--method',choices=['network','idw'],default='network')
    args=parser.parse_args()
    checkpoint=torch.load(args.checkpoint,map_location='cpu',weights_only=True)
    norm=checkpoint['normalization'];torch.set_num_threads(checkpoint['config']['threads'])
    target=np.full((ROWS,COLS),np.nan,np.float32);observed=np.zeros((ROWS,COLS),bool)
    with args.points.open(encoding='utf-8-sig',newline='') as stream:
        for row in csv.DictReader(stream):
            point=row['point_id'].strip().upper()
            if len(point)!=3 or point[0]!='P' or not point[1:].isdigit():raise ValueError(point)
            r,c=int(point[1])-1,int(point[2])-1
            if not (0<=r<ROWS and 0<=c<COLS) or observed[r,c]:raise ValueError('Duplicate or invalid point: '+point)
            value=float(row['rssi_dbm'])
            if not np.isfinite(value) or not -127<=value<0:raise ValueError('Invalid RSSI: '+str(value))
            target[r,c]=value;observed[r,c]=True
    if not observed.any():raise ValueError('At least one observation is required')
    x,baseline=features(target,observed,norm['mean_dbm'],norm['std_db'])
    if args.method=='idw':prediction=baseline
    else:
        model=CompletionNet(checkpoint['kind']);model.load_state_dict(checkpoint['state_dict']);model.eval()
        with torch.inference_mode():
            prediction=model(torch.from_numpy(x[None]))[0,0,:ROWS,:COLS].numpy()*norm['std_db']+norm['mean_dbm']
    assert np.isfinite(prediction).all()
    prediction[observed]=target[observed]
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=['point_id','x_m','y_m','rssi_dbm','source'])
        writer.writeheader()
        for r in range(ROWS):
            for c in range(COLS):
                writer.writerow({'point_id':f'P{r+1}{c+1}','x_m':(c+.5)*8.23/COLS,
                    'y_m':(r+.5)*6.95/ROWS,'rssi_dbm':float(prediction[r,c]),
                    'source':'observed' if observed[r,c] else args.method+'_predicted'})
    print(f'Loaded {checkpoint["kind"]}; {observed.sum()} observed points; wrote 63 grid values: {args.output}')

if __name__=='__main__':main()
