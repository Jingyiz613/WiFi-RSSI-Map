"""Optional Sionna RT comparison: rectangular room with/without one panel."""
import math
from threading import Lock

_lock = Lock()
MATERIALS = {"wood", "chipboard", "plywood", "glass", "metal", "plasterboard", "concrete"}


def compare(scene_data, request):
    material = request["material"]
    thickness = float(request["thickness_mm"]) / 1000
    frequency = float(request["frequency_ghz"])
    tx_h, rx_h = float(request["tx_height"]), float(request["rx_height"])
    if material not in MATERIALS or not (0 < thickness <= .5 and 2.4 <= frequency <= 5.9):
        raise ValueError("精算需要已知材料、正板厚和 2.4–5.9 GHz 频率")
    if not (0 < tx_h < 2.8 and 0 < rx_h < 2.8):
        raise ValueError("精算收发高度须在简化房间 0–2.8 m 内")
    if len(scene_data["cells"]) > 100 or not scene_data["cells"]:
        raise ValueError("精算支持 1–100 个网格")
    item = next((f for f in scene_data["furniture"] if f["index"] == request["index"]), None)
    if item is None or not (0 < item["height_m"] < 2.8):
        raise ValueError("请选中一件有效家具")
    with _lock:
        return _solve(scene_data, item, material, thickness, frequency, tx_h, rx_h)


def _solve(data, item, material, thickness, frequency, tx_h, rx_h):
    import numpy as np
    from sionna.rt import PlanarArray, PathSolver, Receiver, Transmitter, load_scene_from_string

    def plate(name, material_name, depth, center, u, v):
        n = np.cross(u, v)
        matrix = " ".join(str(float(x)) for x in np.column_stack([u,v,n,center]).ravel())
        return (f'<shape type="rectangle" id="{name}"><transform name="to_world">'
                f'<matrix value="{matrix} 0 0 0 1"/></transform>'
                f'<bsdf type="itu-radio-material" id="mat_{name}">'
                f'<string name="type" value="{material_name}"/><float name="thickness" value="{depth}"/>'
                '</bsdf></shape>')

    w, d, h = data["room"]["width_m"], data["room"]["height_m"], 2.8
    x, y, z = (w/2,0,0), (0,d/2,0), (0,0,h/2)
    room = "".join([
        plate("floor","concrete",.15,(w/2,d/2,0),x,y),
        plate("ceiling","plasterboard",.013,(w/2,d/2,h),x,y),
        plate("south","plasterboard",.10,(w/2,0,h/2),x,z),
        plate("north","plasterboard",.10,(w/2,d,h/2),x,z),
        plate("west","plasterboard",.10,(0,d/2,h/2),y,z),
        plate("east","plasterboard",.10,(w,d/2,h/2),y,z),
    ])
    width, depth, height = item["width_m"], item["depth_m"], item["height_m"]
    panel = plate("furniture_panel",material,thickness,(item["cx"],item["cz"],height/2),
                  (width/2,0,0) if width >= depth else (0,depth/2,0),(0,0,height/2))
    ap = data["ap"]
    cells = [c for c in data["cells"] if c["label"] != ap["label"] and not (
        item["x1"] <= c["x_m"] <= item["x2"] and item["y1"] <= c["y_m"] <= item["y2"] and rx_h <= height)]
    if not cells:
        raise ValueError("没有共同可用测点")
    if item["x1"] <= ap["x_m"] <= item["x2"] and item["y1"] <= ap["y_m"] <= item["y2"] and tx_h <= height:
        raise ValueError("路由器位于家具内，不能做前后对比")

    def solve(extra):
        scene = load_scene_from_string('<scene version="3.0.0">'+room+extra+'</scene>',merge_shapes=False)
        scene.frequency = frequency*1e9
        scene.tx_array = scene.rx_array = PlanarArray(num_rows=1,num_cols=1,pattern="iso",polarization="V")
        scene.add(Transmitter(name="ap",position=[ap["x_m"],ap["y_m"],tx_h]))
        for i,c in enumerate(cells):
            scene.add(Receiver(name=f"rx{i}",position=[c["x_m"],c["y_m"],rx_h]))
        paths = PathSolver()(scene,max_depth=3,samples_per_src=50000,max_num_paths_per_src=100000,
                             diffraction=True,edge_diffraction=True,seed=42)
        a,_ = paths.cir(out_type="numpy")
        return np.sum(np.abs(a[:,0,0,0,:,0])**2,axis=1)

    before,after=solve(""),solve(panel)
    rows=[]
    for cell,a,b in zip(cells,before,after,strict=True):
        delta=10*math.log10(float(b)/float(a)) if a>0 and b>0 and np.isfinite([a,b]).all() else None
        rows.append({"label":cell["label"],"delta_db":delta})
    return {"rows":rows,"model":"sionna-rt-2.2.0","scope":"rectangular_room_single_center_panel"}
