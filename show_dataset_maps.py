"""Show ten measured maps with current v2 scene roles; never interpolate missing values."""
from pathlib import Path
import csv
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'output/ten_scene_maps_v2'

def read(p):
    with p.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    rows=read(ROOT/'output/training_tables_v1/training_points.csv')
    extra=read(ROOT/'output/rss_archive/AP_P74_20261021_140830/WiFi_RSS_P74_20261021/rss_summary_20261021_140830.csv')
    rows.extend({**r,'ap_point':'P74'} for r in extra)
    order=['P16','P22','P28','P44','P62','P68','P74','P64','P49','P26']
    plt.rcParams.update({'font.sans-serif':['Microsoft YaHei','SimHei','DejaVu Sans'],'axes.unicode_minus':False,'font.size':10})
    cmap=plt.colormaps['viridis'].copy();cmap.set_bad('#e5e8ec')
    def draw(ax,ap,values=False):
        sr=[r for r in rows if r['ap_point']==ap]
        grid=np.full((7,9),np.nan)
        for r in sr:grid[int(r['row'])-1,int(r['column'])-1]=float(r['rssi_median_dbm'])
        n=np.isfinite(grid).sum();assert n==(61 if ap=='P62' else 62)
        role='验证集' if ap=='P64' else '重复测试对照' if ap in ('P49','P26') else '训练集'
        color='#ca873b' if ap=='P64' else '#97629c' if ap in ('P49','P26') else '#28799b'
        im=ax.imshow(grid,origin='lower',extent=[0,8.23,0,6.95],vmin=-55,vmax=-10,cmap=cmap,interpolation='nearest',aspect='equal')
        ax.set_title(f'AP {ap} · {role} · {n}个有效实测点',color=color,fontsize=13,pad=9,fontweight='bold')
        ax.set_xlabel('x / m（向东）');ax.set_ylabel('y / m（向北）')
        for spine in ax.spines.values():spine.set_edgecolor(color);spine.set_linewidth(1.5)
        ax.scatter((int(ap[2])-.5)*8.23/9,(int(ap[1])-.5)*6.95/7,marker='*',s=170,c='#ef354b',edgecolor='white',linewidth=.7,zorder=4)
        if ap=='P62':
            ax.text(.5*8.23/9,.5*6.95/7,'试测\n未纳入',ha='center',va='center',fontsize=8,color='#6f7780')
            for p in ('P21','P22'):
                i,j=int(p[1])-1,int(p[2])-1
                ax.add_patch(Rectangle((j*8.23/9,i*6.95/7),8.23/9,6.95/7,fill=False,edgecolor='#ef9348',lw=2))
        if values:
            for r in sr:
                i,j=int(r['row'])-1,int(r['column'])-1;v=grid[i,j]
                ax.text((j+.5)*8.23/9,(i+.5)*6.95/7,f'{r["point_id"]}\n{v:g}',ha='center',va='center',fontsize=7.5,color='black' if v>-27 else 'white')
        return im
    fig,axes=plt.subplots(5,2,figsize=(14,25),layout='constrained')
    for ax,ap in zip(axes.flat,order):im=draw(ax,ap)
    fig.suptitle('十组实测 RSSI 地图｜按第二版训练方案标注\n训练7组 · 验证1组 · 重复测试对照2组',fontsize=21)
    cb=fig.colorbar(im,ax=axes.ravel().tolist(),shrink=.55,pad=.025,aspect=45);cb.set_label('实测 RSSI 中位数 / dBm（越接近0越强）',fontsize=12)
    fig.supxlabel('同一色标：−55 至 −10 dBm；每格表示网格中心的实测值，未做插值。灰色＝没有纳入的测量，红星＝路由器。\nP62：P11试测替代值与AP占位记录排除，橙框P21/P22分别为9/5次有效读数。\nP49/P26已重复使用，仅作固定对照，结论独立性有限。房间8.23 m × 6.95 m，西南角原点。',fontsize=11)
    fig.savefig(OUT/'十组实测地图_第二版数据划分.png',dpi=150);plt.close(fig)
    for ap in order:
        fig,ax=plt.subplots(figsize=(10,8),layout='constrained');im=draw(ax,ap,True)
        fig.colorbar(im,ax=ax,shrink=.85,label='实测中位数 RSSI / dBm')
        note='P49/P26已重复用于评分，独立性有限。' if ap in ('P49','P26') else '按当前第二版训练方案标注。'
        fig.supxlabel('灰色＝未纳入，红星＝路由器；每格仅表示中心实测值。'+note,fontsize=10)
        fig.savefig(OUT/f'{ap}_实测与用途.png',dpi=160);plt.close(fig)
    (OUT/'数据划分说明.md').write_text('''# 当前第二版数据划分

- 训练集：P16、P22、P28、P44、P62、P68、P74，共7组433个有效测点。
- 验证集：P64，共1组62点；用于选择最佳权重和调整方案。
- 重复测试对照：P49、P26，共2组124点；已经查看过结果，结论独立性有限。
- 十组图均为实测中位数图，不是网络预测或插值图；共619个可用点。
- P62只展示61个可用点：AP占位P62的记录排除，P11试测替代值不纳入。P21/P22分别9/5次有效读数，橙框提示。
- 第一版训练没有P74，其余划分相同。本图不是队友的另一套划分。
- 统一色标-55至-10 dBm，西南角原点，7行9列。
''',encoding='utf-8')
    print('Generated overview and 10 detailed measured maps:',OUT)

if __name__=='__main__':main()
