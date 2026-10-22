import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

ROWS, COLS = 7, 9
PADDED = (8, 12)
Y, X = np.meshgrid((np.arange(ROWS)+.5)*6.95/ROWS,
                   (np.arange(COLS)+.5)*8.23/COLS, indexing='ij')
XY = np.column_stack([X.ravel(), Y.ravel()])
DIST2 = ((XY[:, None, :] - XY[None, :, :])**2).sum(2)

def observation_mask(target, count, distribution, rng):
    available = np.flatnonzero(np.isfinite(target.ravel()))
    if distribution == 'uniform':
        selected = rng.choice(available, count, replace=False)
    elif distribution == 'clustered':
        anchor = rng.choice(available)
        selected = available[np.argsort(DIST2[anchor, available] + rng.uniform(0, .01, len(available)))[:count]]
    else:
        raise ValueError(distribution)
    mask = np.zeros(ROWS*COLS, bool)
    mask[selected] = True
    return mask.reshape(ROWS, COLS)

def features(target, observed, mean, std):
    assert observed.shape == (ROWS, COLS) and observed.any()
    values = target[observed]
    assert np.isfinite(values).all()
    weights = 1 / (DIST2[:, observed.ravel()] + .15**2)
    baseline = (weights @ values / weights.sum(1)).reshape(ROWS, COLS)
    array = np.zeros((4, *PADDED), np.float32)
    array[0, :ROWS, :COLS][observed] = (values - mean) / std
    array[1, :ROWS, :COLS] = observed
    array[2, :ROWS, :COLS] = 1
    array[3, :ROWS, :COLS] = (baseline - mean) / std
    return array, baseline

def block(inputs, outputs):
    return nn.Sequential(nn.Conv2d(inputs, outputs, 3, padding=1), nn.ReLU(),
                         nn.Conv2d(outputs, outputs, 3, padding=1), nn.ReLU())

class CompletionNet(nn.Module):
    """Two-level small encoder/decoder; skip connections for U-Net variants."""
    def __init__(self, kind, width=8):
        super().__init__()
        assert kind in ['autoencoder', 'unet', 'residual_unet']
        self.kind = kind
        self.skips = kind != 'autoencoder'
        self.e1 = block(4 if kind == 'residual_unet' else 3, width)
        self.e2 = block(width, width*2)
        self.bridge = block(width*2, width*4)
        self.d2 = block(width*4 + (width*2 if self.skips else 0), width*2)
        self.d1 = block(width*2 + (width if self.skips else 0), width)
        self.head = nn.Conv2d(width, 1, 1)
        if kind == 'residual_unet':
            nn.init.zeros_(self.head.weight)
            nn.init.zeros_(self.head.bias)

    def forward(self, x):
        a = self.e1(x if self.kind == 'residual_unet' else x[:, :3])
        b = self.e2(F.avg_pool2d(a, 2))
        c = self.bridge(F.avg_pool2d(b, 2))
        d = F.interpolate(c, size=b.shape[-2:], mode='bilinear', align_corners=False)
        d = self.d2(torch.cat([d,b], dim=1) if self.skips else d)
        e = F.interpolate(d, size=a.shape[-2:], mode='bilinear', align_corners=False)
        result = self.head(self.d1(torch.cat([e,a], dim=1) if self.skips else e))
        if self.kind == 'residual_unet':
            result = result + x[:, 3:4]
        return result
