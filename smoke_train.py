"""One-scene training diagnostic, not a generalization evaluation."""
from pathlib import Path
import csv
import json
import torch
from torch import nn
from torch.nn import functional as F


class SmallUNet(nn.Module):
    def __init__(self):
        super().__init__()
        def block(a, b):
            return nn.Sequential(nn.Conv2d(a,b,3,padding=1),nn.ReLU(),
                                 nn.Conv2d(b,b,3,padding=1),nn.ReLU())
        self.encoder = block(3, 16)
        self.bottom = block(16, 32)
        self.decoder = block(48, 16)
        self.output = nn.Conv2d(16, 1, 1)

    def forward(self, x):
        skip = self.encoder(x)
        low = self.bottom(F.max_pool2d(skip, 2))
        up = F.interpolate(low, size=skip.shape[-2:], mode='bilinear', align_corners=False)
        return self.output(self.decoder(torch.cat([skip, up], dim=1)))


def main():
    root = Path(__file__).resolve().parent
    torch.manual_seed(42)
    torch.set_num_threads(2)
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    with (root/'output/training_tables_v1/training_points.csv').open(encoding='utf-8-sig',newline='') as f:
        rows = [r for r in csv.DictReader(f) if r['ap_point']=='P22' and r['split']=='train']
    assert len(rows)==62
    values = torch.tensor([float(r['rssi_median_dbm']) for r in rows])
    mean, scale = values.mean(), values.std().clamp_min(1)
    target = torch.zeros(1,1,7,9)
    observed = torch.zeros_like(target)
    for r in rows:
        i,j = int(r['row'])-1,int(r['column'])-1
        target[0,0,i,j]=(float(r['rssi_median_dbm'])-mean)/scale
        observed[0,0,i,j]=1
    selected = torch.randperm(len(rows))[:20].tolist()
    input_mask=torch.zeros_like(target)
    for k in selected:
        r=rows[k]
        input_mask[0,0,int(r['row'])-1,int(r['column'])-1]=1
    hidden=observed-input_mask
    x=torch.cat([target*input_mask,input_mask,torch.ones_like(target)],dim=1).to(device)
    target,hidden=target.to(device),hidden.to(device)
    model=SmallUNet().to(device)
    optimizer=torch.optim.Adam(model.parameters(),lr=.003)
    def loss_value():
        return (((model(x)-target)**2)*hidden).sum()/hidden.sum()
    model.eval()
    with torch.no_grad(): initial=loss_value().item()
    print('Device:',device,flush=True)
    print('Diagnostic only: P22 / 20 input points / 42 hidden measured points',flush=True)
    print(f'Initial RMSE: {initial**.5*scale.item():.3f} dB',flush=True)
    model.train()
    for step in range(1,201):
        optimizer.zero_grad()
        loss=loss_value()
        if not torch.isfinite(loss):raise RuntimeError('Non-finite loss')
        loss.backward()
        optimizer.step()
        if step%50==0:print(f'Step {step}/200',flush=True)
    model.eval()
    with torch.no_grad(): final=loss_value().item()
    assert model(x).shape==(1,1,7,9)
    result={'diagnostic_only':True,'scene':'P22','input_point_ids':[rows[k]['point_id'] for k in selected],
            'device':str(device),'initial_rmse_db':initial**.5*scale.item(),
            'final_rmse_db':final**.5*scale.item(),'steps':200,
            'passed':final<initial,'note':'Same fixed hidden points used for optimization. Not a validation/test score.'}
    out=root/'output/smoke_training'
    out.mkdir(parents=True,exist_ok=True)
    (out/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(f'Final RMSE: {result["final_rmse_db"]:.3f} dB',flush=True)
    print('SMOKE TRAIN PASSED' if result['passed'] else 'SMOKE TRAIN FAILED',flush=True)
    print('This is a program check, not final model accuracy.',flush=True)
    if not result['passed']:raise RuntimeError('Loss did not decrease')


if __name__=='__main__':
    main()
