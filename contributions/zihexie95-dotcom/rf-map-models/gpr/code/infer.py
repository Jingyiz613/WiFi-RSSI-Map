"""Deploy from a JSON model, AP grid location, and sparse observed points only."""
import argparse,csv,json
from pathlib import Path
import numpy as np
from gpr import XY,ap_position,predict

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--model',type=Path,required=True)
    parser.add_argument('--points',type=Path,required=True,help='CSV fields point_id,rssi_dbm')
    parser.add_argument('--ap',required=True,help='Known AP point, e.g. P74')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();model=json.loads(args.model.read_text(encoding='utf-8'))
    points=[];values=[];seen=set()
    with args.points.open(encoding='utf-8-sig',newline='') as stream:
        for row in csv.DictReader(stream):
            point=row['point_id'].strip().upper()
            xy=ap_position(point);value=float(row['rssi_dbm'])
            if point in seen or point==args.ap.upper():raise ValueError('Duplicate point or AP occupied cell: '+point)
            if not np.isfinite(value) or not -127<=value<0:raise ValueError('Invalid RSSI')
            seen.add(point);points.append(xy);values.append(value)
    if not points:raise ValueError('No observed points')
    means,variance=predict(model,np.array(points),np.array(values),XY,ap_position(args.ap.upper()))
    input_values={tuple(xy):value for xy,value in zip(points,values)}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=['point_id','x_m','y_m','rssi_dbm','source','prediction_sd_db','prediction95_low_dbm','prediction95_high_dbm'])
        writer.writeheader()
        for i,xy in enumerate(XY):
            observed=tuple(xy) in input_values
            mean=input_values[tuple(xy)] if observed else float(means[i])
            sd=float(np.sqrt(variance[i])) if variance is not None and not observed else None
            writer.writerow({'point_id':f'P{i//9+1}{i%9+1}','x_m':xy[0],'y_m':xy[1],
                             'rssi_dbm':mean,'source':'observed' if observed else 'predicted',
                             'prediction_sd_db':'' if sd is None else sd,
                             'prediction95_low_dbm':'' if sd is None else mean-1.96*sd,
                             'prediction95_high_dbm':'' if sd is None else mean+1.96*sd})
    print(f'Loaded {model["kind"]}; AP={args.ap.upper()}; input={len(values)}; output={args.output}')

if __name__=='__main__':main()
