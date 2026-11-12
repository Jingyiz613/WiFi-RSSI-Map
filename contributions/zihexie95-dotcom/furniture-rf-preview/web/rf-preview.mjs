// ITU-R P.2040-4 coefficients (a,b,c,d); subset valid across 2.4–5.9 GHz.
// Source: NVlabs/sionna-rt, deddf837, src/sionna/rt/radio_materials/itu.py (Apache-2.0).
const materials = {
  wood: [1.99, 0, .0047, 1.0718],
  chipboard: [2.58, 0, .0217, .78],
  plywood: [2.71, 0, .33, 0],
  glass: [6.31, 0, .0036, 1.3394],
  metal: [1, 0, 1e7, 0],
  plasterboard: [2.73, 0, .0085, .9395],
  concrete: [5.24, 0, .0462, .7822],
};
const add = ([a,b],[c,d]) => [a+c,b+d];
const sub = ([a,b],[c,d]) => [a-c,b-d];
const mul = ([a,b],[c,d]) => [a*c-b*d,a*d+b*c];
const power = ([a,b]) => a*a+b*b;
const div = ([a,b],[c,d]) => {
  const norm = c*c+d*d;
  return [(a*c+b*d)/norm,(b*c-a*d)/norm];
};

// Air–slab–air, Sionna EM primer eqs. 33–35. Average TE/TM power because
// the current editor does not specify antenna polarization.
export function slabLoss(material, thicknessMm, frequencyGhz, incidenceCos) {
  if (thicknessMm === 0) return 0;
  const [a,b,c,d] = materials[material];
  const eps = [a*frequencyGhz**b, -c*frequencyGhz**d/(2*Math.PI*frequencyGhz*1e9*8.8541878128e-12)];
  const cos = Math.max(1e-6, Math.min(1, incidenceCos));
  const re = eps[0] - 1 + cos*cos;
  const rootRe = Math.sqrt((Math.hypot(re,eps[1])+re)/2);
  const gamma = [rootRe, eps[1]/(2*rootRe)];
  const scale = 2*Math.PI*frequencyGhz*1e9/299792458 * thicknessMm/1000;
  const attenuation = Math.exp(scale*gamma[1]);
  const phase = scale*gamma[0];
  const wave = [attenuation*Math.cos(phase), -attenuation*Math.sin(phase)];
  const one = [1,0], wave2 = mul(wave,wave);
  const interfacePower = (impedance) => {
    const r = div(sub(impedance,gamma),add(impedance,gamma));
    const r2 = mul(r,r);
    return power(div(mul(sub(one,r2),wave),sub(one,mul(r2,wave2))));
  };
  const transmitted = (interfacePower([cos,0])+interfacePower(mul(eps,[cos,0])))/2;
  return -10*Math.log10(Math.max(1e-12,Math.min(1,transmitted)));
}

// The selected object's box supplies an entry plane, not the physical thickness.
export function furnitureLoss(ap, cell, item, txHeight, rxHeight, prior) {
  if (cell.label === ap.label) return null;
  const from = [ap.x_m, ap.y_m, txHeight];
  const to = [cell.x_m, cell.y_m, rxHeight];
  const low = [item.x1, item.y1, 0];
  const high = [item.x2, item.y2, item.height_m];
  if (to.every((v, i) => v >= low[i] && v <= high[i])) return null;
  if (from.every((v, i) => v >= low[i] && v <= high[i])) return null;
  let start = 0, end = 1, entryAxis = 0;
  for (let i = 0; i < 3; i++) {
    const delta = to[i] - from[i];
    if (Math.abs(delta) < 1e-9) {
      if (from[i] <= low[i] || from[i] >= high[i]) return 0;
      continue;
    }
    const a = (low[i] - from[i]) / delta;
    const b = (high[i] - from[i]) / delta;
    if (Math.min(a,b) > start) {
      start = Math.min(a,b);
      entryAxis = i;
    }
    end = Math.min(end, Math.max(a, b));
    if (end <= start) return 0;
  }
  if (typeof prior !== 'function') return prior;
  const direction = to.map((v,i) => v-from[i]);
  return prior(Math.abs(direction[entryAxis])/Math.hypot(...direction));
}
