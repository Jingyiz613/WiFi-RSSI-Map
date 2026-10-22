"""Publication-style figures from saved measurements and predictions only."""
from pathlib import Path
import csv,json
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'output/experiment_visuals_20261021'

def read(p):
    with p.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    plt.rcParams.update({'font.sans-serif':['Microsoft YaHei','SimHei','DejaVu Sans'],
        'axes.unicode_minus':False,'font.size':11,'savefig.facecolor':'white'})
    rows=read(ROOT/'output/training_tables_v1/training_points.csv')
    predictions=read(ROOT/'output/reused_test_v1_v2/predictions.csv')
    selections=json.loads((ROOT/'output/final_test_v1/frozen_plan.json').read_text())['fixed_inputs']
    figures=[]
    for ap in ('P49','P26'):
        ids=selections[ap][0]
        sr=[r for r in rows if r['ap_point']==ap]
        actual=np.full((7,9),np.nan);inputs=actual.copy();pred=actual.copy();error=actual.copy()
        for r in sr:
            i,j=int(r['row'])-1,int(r['column'])-1
            actual[i,j]=float(r['rssi_median_dbm'])
            if r['point_id'] in ids:inputs[i,j]=actual[i,j]
        for r in predictions:
            if r['ap']==ap and r['trial']=='1' and r['method']=='residual_v2':
                i,j=int(r['point_id'][1])-1,int(r['point_id'][2])-1
                pred[i,j]=float(r['predicted_dbm']);error[i,j]=abs(float(r['error_db']))
        assert np.isfinite(error).sum()==42 and np.isfinite(inputs).sum()==20
        fig,axes=plt.subplots(2,2,figsize=(13,10),layout='constrained')
        cmap=plt.colormaps['viridis'].copy();cmap.set_bad('#e8ebef')
        titles=['给模型的20个实测点','全部62个实测点（参考）','第二版预测：只展示42个隐藏实测点','42个隐藏点的绝对误差']
        for ax,data,title in zip(axes.flat,[inputs,actual,pred,error],titles):
            iserror='误差' in title
            cm=plt.colormaps['YlOrRd'].copy() if iserror else cmap
            cm.set_bad('#e8ebef')
            im=ax.imshow(data,origin='lower',extent=[0,8.23,0,6.95],vmin=0 if iserror else -55,
                         vmax=15 if iserror else -10,cmap=cm,interpolation='nearest',aspect='equal')
            ax.set_title(title,fontsize=12,pad=10);ax.set_xlabel('x / m（向东）');ax.set_ylabel('y / m（向北）')
            ax.scatter((int(ap[2])-.5)*8.23/9,(int(ap[1])-.5)*6.95/7,marker='*',s=130,c='#ec294b',edgecolors='white',linewidths=.6)
            for r in sr:
                i,j=int(r['row'])-1,int(r['column'])-1
                if np.isfinite(data[i,j]):
                    value=data[i,j];ax.text((j+.5)*8.23/9,(i+.5)*6.95/7,f'{value:.1f}',ha='center',va='center',fontsize=8,
                        color='black' if iserror or value>-27 else 'white')
            cb=fig.colorbar(im,ax=ax,shrink=.8,extend='max' if iserror else 'neither');cb.set_label('绝对误差 / dB' if iserror else 'RSSI / dBm')
        one_rmse=np.sqrt(np.nanmean(error**2));one_mae=np.nanmean(error)
        fig.suptitle(f'AP {ap}｜少量测点预测信号分布\n固定第1组选点：20点输入、42点评分；本例 MAE {one_mae:.2f} dB，RMSE {one_rmse:.2f} dB',fontsize=16)
        fig.supxlabel('灰色＝此图不展示数值；红星＝路由器。网格颜色代表点位数值，不代表格内每处均已测量。\nP49/P26为重复使用的测试数据，独立性有限；本例按预先固定第1组选点展示，未挑选最好案例。',fontsize=10)
        figures.append((f'01_{ap}_输入实测预测误差',fig))
    v1=json.loads((ROOT/'output/residual_runs/20261021_134850_642128/summary.json').read_text())
    v2=json.loads((ROOT/'output/residual_v2_runs/20261021_154833_150288/summary.json').read_text())
    ae=json.loads((ROOT/'output/autoencoder_runs/20261021_135427_799438/summary.json').read_text())
    un=json.loads((ROOT/'output/unet_runs/20261021_134356_519488/summary.json').read_text())
    old=json.loads((ROOT/'output/final_test_v1/summary.json').read_text())['metrics']
    new=json.loads((ROOT/'output/reused_test_v1_v2/summary.json').read_text())['metrics']
    labels=['距离估算','U-Net','自编码器','残差第一版','残差第二版']
    validation=[v1['idw_rmse_db'],un['validation_rmse_db'],ae['validation_rmse_db'],v1['residual_rmse_db'],v2['residual_rmse_db']]
    test=[next(r['rmse_db'] for r in old if r['scene']=='combined' and r['method']==m) for m in ['idw','unet','autoencoder','residual_unet']]
    test.append(next(r['rmse_db'] for r in new if r['scene']=='combined' and r['method']=='residual_v2'))
    fig,axes=plt.subplots(1,2,figsize=(14,6),layout='constrained')
    colors=['#65758b','#70a5c4','#95b6a4','#b6a5c5','#226e91']
    for ax,data,title in zip(axes,[validation,test],['P64 验证（用于模型选择）','P49＋P26 重复测试对照']):
        bars=ax.bar(labels,data,color=colors,width=.65)
        ax.bar_label(bars,fmt='%.3f',padding=5,fontsize=11)
        ax.set_ylim(0,8.5);ax.set_ylabel('RMSE / dB（越低越好）');ax.set_title(title,fontsize=14)
        ax.tick_params(axis='x',rotation=20);ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True)
        ax.spines[['top','right']].set_visible(False)
    fig.suptitle('不同方法的信号预测误差',fontsize=18)
    fig.supxlabel('每场景10组固定20点输入，仅隐藏实测点评分。验证与测试角色不同，分别展示。\n第二版仅为残差网络增加P74训练数据；重复测试独立性有限，三类网络的优化设置也并非完全相同。',fontsize=10)
    figures.append(('02_各方法误差比较',fig))
    fig,axes=plt.subplots(1,2,figsize=(14,6),layout='constrained')
    for ax,folder,title in zip(axes,['output/residual_runs/20261021_134850_642128','output/residual_v2_runs/20261021_154833_150288'],['第一版残差网络｜6组训练','第二版残差网络｜7组训练（增加P74）']):
        history=read(ROOT/folder/'history.csv');summary=json.loads((ROOT/folder/'summary.json').read_text())
        epochs=[int(r['epoch']) for r in history]
        ax.plot(epochs,[float(r['train_rmse_db']) for r in history],label='训练 RMSE',color='#28799f')
        ax.plot(epochs,[float(r['validation_rmse_db']) for r in history],label='P64 验证 RMSE',color='#dd8240')
        ax.axhline(summary['idw_rmse_db'],ls='--',color='#65758b',label='P64 距离估算')
        ax.scatter(summary['best_epoch'],summary['residual_rmse_db'],color='#bc3545',s=55,zorder=4,label=f'最佳第{summary["best_epoch"]}轮')
        ax.set_title(title);ax.set_xlabel('训练轮次');ax.set_ylabel('RMSE / dB');ax.set_ylim(0,8)
        ax.legend(fontsize=9);ax.grid(alpha=.2);ax.spines[['top','right']].set_visible(False)
    fig.suptitle('训练过程：训练误差下降，不代表新部署预测更好',fontsize=17)
    fig.supxlabel('训练曲线为每轮随机抽点、更新过程中的误差；验证曲线用固定输入点，二者并非相同评分条件。\n最佳权重按P64验证结果保存，连续40轮未改善后停止；图中结果不是独立测试。',fontsize=10)
    figures.append(('03_两版训练曲线',fig))
    for name,fig in figures:fig.savefig(OUT/(name+'.png'),dpi=180)
    with PdfPages(OUT/'实验结果可视化汇总.pdf') as pdf:
        for _,fig in figures:pdf.savefig(fig)
    for _,fig in figures:plt.close(fig)
    (OUT/'展示说明.md').write_text('''# 课堂展示说明

1. P49/P26四宫格：说明只输入20点，预测其余42个实测点；实测参考、预测和误差分开，三幅RSSI图共用色标。灰色不代表0信号，AP占位点没有真实答案。
2. 方法比较：第二版残差网络在P64验证略好，但在重复测试P49/P26上更差。目前没有证据支持网络稳定优于距离估算。
3. 训练曲线：训练误差降低而验证误差上升，提示对新部署的泛化有限；不能单靠延长训练解决。

数字全部来自已有实际运行。四宫格固定展示第1组输入，未按好坏挑选。重复抽点不产生新独立场景，测试已经重复使用，结论独立性有限。只覆盖同一房间，不声称跨房间有效。
''',encoding='utf-8')
    print('Saved',len(figures),'PNG figures and one combined PDF:',OUT)


if __name__=='__main__':main()
