import csv,hashlib,json,shutil
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import BoundaryNorm,ListedColormap
from matplotlib.patches import Patch,Rectangle

SOURCE=Path(__file__).resolve().parents[1]
OUT=SOURCE/'results/five_levels'
OUT.mkdir(parents=True,exist_ok=True)
BOUNDARIES=np.array([-40.,-35.,-30.,-25.])
LABELS=['极差','较差','一般','较好','极好']
COLORS=['#d9544d','#f29b4b','#f1d057','#91cf72','#39aa6b']
METHODS=['idw','gpr','pathloss','pathloss_gpr','autoencoder','unet','residual_unet']
NAMES={'idw':'IDW','gpr':'普通GPR','pathloss':'纯路径损耗','pathloss_gpr':'路径损耗＋GPR',
       'autoencoder':'卷积自编码器','unet':'小型U-Net','residual_unet':'残差U-Net'}

def read_csv(path):return list(csv.DictReader(path.open(encoding='utf-8-sig',newline='')))
def write_csv(path,rows):
    with path.open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
def grade(values):
    values=np.asarray(values);valid=np.isfinite(values)
    result=np.full(values.shape,-1,dtype=int)
    result[valid]=np.digitize(values[valid],BOUNDARIES,right=False)
    return result
assert grade(np.array([-41.,-40.,-35.,-30.,-25.,np.nan])).tolist()==[0,1,2,3,4,-1]

data=np.load(SOURCE/'neural/data/maps.npz',allow_pickle=False)
maps=dict(zip(data['groups'].tolist(),data['maps']));truth=maps['P74'];truth_grade=grade(truth)
masks=np.load(SOURCE/'neural/data/evaluation_masks.npz',allow_pickle=False)
gp=np.load(SOURCE/'gpr/predictions.npz',allow_pickle=False)
nn=np.load(SOURCE/'neural/predictions.npz',allow_pickle=False)
RUNS=read_csv(SOURCE/'neural/training_runs.csv')
all_predictions={kind:[(0,gp[kind])] for kind in METHODS[:4]}
for kind in METHODS[4:]:all_predictions[kind]=[(seed,nn[f'{kind}_{seed}']) for seed in [20261021,20261022,20261023]]
assert all(a.shape==(120,7,9) for streams in all_predictions.values() for _,a in streams)

def scores(true,pred):
    delta=pred-true;confusion=np.zeros((5,5),dtype=int)
    np.add.at(confusion,(true,pred),1)
    f1=[]
    for i in range(5):
        denominator=confusion[i].sum()+confusion[:,i].sum()
        f1.append(2*confusion[i,i]/denominator if denominator else 0.)
    weights=((np.arange(5)[:,None]-np.arange(5)[None,:])/4.)**2
    expected=np.outer(confusion.sum(1),confusion.sum(0))/confusion.sum()
    denominator=(weights*expected).sum()
    return {'exact_grade_agreement_pct':float((delta==0).mean()*100),
            'within_one_grade_pct':float((np.abs(delta)<=1).mean()*100),
            'mean_abs_grade_error':float(np.abs(delta).mean()),
            'overoptimistic_pct':float((delta>0).mean()*100),'pessimistic_pct':float((delta<0).mean()*100),
            'macro_f1':float(np.mean(f1)),
            'quadratic_weighted_kappa':float(1-(weights*confusion).sum()/denominator) if denominator else 1.,
            'hidden_predictions':len(true)},confusion

by_seed=[];summary=[];confusions=[]
for budget in [6,12,24]:
    for distribution in ['uniform','clustered']:
        tasks=[i for i in range(len(masks['masks'])) if
               (str(masks['split'][i]),int(masks['budgets'][i]),str(masks['distributions'][i]))==('test',budget,distribution)]
        assert len(tasks)==10 and all(str(masks['groups'][i])=='P74' for i in tasks)
        for kind in METHODS:
            results=[]
            for seed,predictions in all_predictions[kind]:
                truths=[];preds=[];raw_errors=[]
                for i in tasks:
                    hidden=np.isfinite(truth)&~masks['masks'][i]
                    assert hidden.sum()==62-budget
                    truths.extend(truth_grade[hidden]);preds.extend(grade(predictions[i])[hidden])
                    raw_errors.extend(predictions[i][hidden]-truth[hidden])
                result,confusion=scores(np.array(truths),np.array(preds))
                result.update(mae_db=float(np.mean(np.abs(raw_errors))),rmse_db=float(np.sqrt(np.mean(np.square(raw_errors)))))
                row={'model':kind,'seed':seed,'budget':budget,'distribution':distribution,'mask_cases':10,**result}
                by_seed.append(row);results.append(result)
                for true_index in range(5):
                    for predicted_index in range(5):
                        confusions.append({'model':kind,'seed':seed,'budget':budget,'distribution':distribution,
                                           'true_grade':LABELS[true_index],'predicted_grade':LABELS[predicted_index],
                                           'count':int(confusion[true_index,predicted_index])})
            row={'model':kind,'budget':budget,'distribution':distribution,'training_seeds':len(results),
                 'mask_cases_per_seed':10,'hidden_predictions_per_seed':results[0]['hidden_predictions']}
            for metric in results[0]:
                if metric=='hidden_predictions':continue
                values=[r[metric] for r in results]
                row[metric]=float(np.mean(values))
                row[metric+'_seed_std']=float(np.std(values,ddof=1)) if len(values)>1 else 0.
            summary.append(row)
write_csv(OUT/'five_level_metrics.csv',summary);write_csv(OUT/'five_level_metrics_by_seed.csv',by_seed)
write_csv(OUT/'confusion_counts.csv',confusions)

task=next(i for i in range(len(masks['masks'])) if
          (str(masks['split'][i]),int(masks['budgets'][i]),str(masks['distributions'][i]),int(masks['repeats'][i]))==('test',12,'uniform',0))
observed=masks['masks'][task];hidden=np.isfinite(truth)&~observed
chosen_predictions={}
for kind in METHODS:
    if kind in METHODS[:4]:chosen_predictions[kind]=gp[kind][task].copy()
    else:
        chosen=min([r for r in RUNS if r['model']==kind],key=lambda r:float(r['validation_rmse_db']))
        chosen_predictions[kind]=nn[f'{kind}_{chosen["seed"]}'][task].copy()
for values in chosen_predictions.values():
    values[observed]=truth[observed]
    values[~np.isfinite(truth)]=np.nan
demo_pred=chosen_predictions['pathloss_gpr'];demo_grade=grade(demo_pred)
case_score,_=scores(truth_grade[hidden],demo_grade[hidden])
point_rows=[]
for r in range(7):
    for c in range(9):
        valid=bool(np.isfinite(truth[r,c]));t=int(truth_grade[r,c]);p=int(demo_grade[r,c])
        point_rows.append({'point_id':f'P{r+1}{c+1}','measured_dbm':float(truth[r,c]) if valid else '',
                           'display_prediction_dbm':float(demo_pred[r,c]) if valid else '',
                           'measured_grade':LABELS[t] if valid else 'AP/未测',
                           'predicted_grade':LABELS[p] if valid else 'AP/不评估',
                           'input_point':bool(observed[r,c]),'hidden_evaluation_point':bool(hidden[r,c]),
                           'grade_difference':p-t if hidden[r,c] else ''})
write_csv(OUT/'P74_demo_per_point.csv',point_rows)

plt.rcParams.update({'font.sans-serif':['Microsoft YaHei','SimHei','DejaVu Sans'],
                     'axes.unicode_minus':False,'font.size':11})
cmap=ListedColormap(COLORS);cmap.set_bad('#e3e6ea');norm=BoundaryNorm(np.arange(-.5,5.5,1),5)
dx,dy=8.23/9,6.95/7
legend_labels=['极差：< −40','较差：−40 ≤ RSSI < −35','一般：−35 ≤ RSSI < −30',
               '较好：−30 ≤ RSSI < −25','极好：≥ −25']
patches=[Patch(facecolor=COLORS[i],label=legend_labels[i]) for i in range(4,-1,-1)]

def draw_map(ax,values,title,annotate=True,mark_errors=False):
    levels=grade(values)
    ax.pcolormesh(np.linspace(0,8.23,10),np.linspace(0,6.95,8),np.ma.masked_where(levels<0,levels),
                  cmap=cmap,norm=norm,edgecolors='white',linewidth=.65)
    for r in range(7):
        for c in range(9):
            x,y=(c+.5)*dx,(r+.5)*dy
            if annotate:
                label=f'P{r+1}{c+1}\n{values[r,c]:.1f}' if levels[r,c]>=0 else f'P{r+1}{c+1}\nAP'
                ax.text(x,y,label,ha='center',va='center',fontsize=7.7)
            if observed[r,c]:ax.plot(x-.29*dx,y+.28*dy,'ko',markersize=3.2,markeredgecolor='white',markeredgewidth=.35)
            if mark_errors and hidden[r,c] and levels[r,c]!=truth_grade[r,c]:
                ax.add_patch(Rectangle((c*dx+.035,r*dy+.035),dx-.07,dy-.07,fill=False,edgecolor='#6b3fa0',linewidth=1.6))
    ax.scatter(3.5*dx,6.5*dy,marker='*',s=140 if annotate else 90,c='#d52128',edgecolors='white',linewidths=.6,zorder=6)
    ax.set(xlim=(0,8.23),ylim=(0,6.95),xlabel='x（m）',ylabel='y（m）',title=title)
    ax.set_aspect('equal')

fig,axes=plt.subplots(1,2,figsize=(15,7.9),sharey=True)
draw_map(axes[0],truth,'实测 RSSI 五档图')
draw_map(axes[1],demo_pred,f'路径损耗＋GPR 预测五档图\n本示例隐藏点档位一致率 {case_score["exact_grade_agreement_pct"]:.1f}%',mark_errors=True)
fig.suptitle('P74 实测与预测对比 · 本实验相对五档',fontsize=17,y=.975)
fig.legend(handles=patches,loc='lower center',bbox_to_anchor=(.5,.07),ncol=5,fontsize=9,frameon=False)
fig.text(.5,.035,'单位 dBm。黑圆点为12个输入点；紫框为隐藏点错档；红星为AP，灰格不分档、不计分。',ha='center',fontsize=10)
fig.text(.5,.01,'固定第0号输入掩码：50个隐藏实测点。相对档位仅用于本房间比较，不代表通用联网质量。',ha='center',fontsize=10)
fig.subplots_adjust(left=.05,right=.98,bottom=.19,top=.85,wspace=.12)
fig.savefig(OUT/'P74_measured_vs_predicted_five_levels.png',dpi=200);plt.close(fig)

fig,axes=plt.subplots(2,4,figsize=(16,8.2),sharex=True,sharey=True)
draw_map(axes.flat[0],truth,'实测参考五档',annotate=False)
for ax,kind in zip(list(axes.flat)[1:],METHODS):
    score,_=scores(truth_grade[hidden],grade(chosen_predictions[kind])[hidden])
    draw_map(ax,chosen_predictions[kind],NAMES[kind]+f'\n本例一致率 {score["exact_grade_agreement_pct"]:.1f}%',annotate=False)
fig.suptitle('同一 P74 样例、同一12个输入点：七种预测方法的相对五档图',fontsize=16)
fig.legend(handles=patches,loc='lower center',bbox_to_anchor=(.5,.045),ncol=5,fontsize=9,frameon=False)
fig.text(.5,.015,'固定第0号掩码；显示的网络种子仅按验证误差选择。图上输入格保留实测值，指标只算隐藏点。',ha='center',fontsize=10)
fig.subplots_adjust(left=.04,right=.98,top=.87,bottom=.17,wspace=.15,hspace=.36)
fig.savefig(OUT/'P74_all_models_five_levels.png',dpi=180);plt.close(fig)

fig,axes=plt.subplots(1,2,figsize=(12,5.7))
confusion_matrices=[]
for kind in ['idw','pathloss_gpr']:
    entries=[r for r in confusions if (r['model'],r['budget'],r['distribution'])==(kind,12,'uniform')]
    matrix=np.zeros((5,5),int)
    for row in entries:matrix[LABELS.index(row['true_grade']),LABELS.index(row['predicted_grade'])]=row['count']
    assert matrix.sum()==500
    confusion_matrices.append(matrix[::-1,::-1])
maximum=max(m.max() for m in confusion_matrices)
for ax,kind,matrix in zip(axes,['idw','pathloss_gpr'],confusion_matrices):
    artist=ax.imshow(matrix,cmap='Blues',vmin=0,vmax=maximum)
    accuracy=100*np.trace(matrix)/matrix.sum()
    ax.set(xticks=np.arange(5),yticks=np.arange(5),xticklabels=LABELS[::-1],yticklabels=LABELS[::-1],
           xlabel='预测档位',ylabel='实测档位',title=NAMES[kind]+f' · 档位一致率 {accuracy:.1f}%')
    for r in range(5):
        for c in range(5):ax.text(c,r,str(matrix[r,c]),ha='center',va='center',color='white' if matrix[r,c]>maximum*.55 else '#263344')
fig.suptitle('五档混淆矩阵 · 12个均匀输入点 × 10个固定掩码',fontsize=15)
fig.text(.5,.025,'每幅矩阵500次隐藏点预测，来自同一地图的重叠位置，并非500个独立位置或场景。',ha='center',fontsize=10)
fig.subplots_adjust(left=.07,right=.9,top=.84,bottom=.14,wspace=.3)
fig.colorbar(artist,cax=fig.add_axes([.92,.2,.015,.57]),label='次数')
fig.savefig(OUT/'P74_five_level_confusion.png',dpi=180);plt.close(fig)

definition={'kind':'user-approved relative grades for this experiment; not a universal Wi-Fi quality standard',
            'grades':[{'label':LABELS[i],'class_index':i,'colour':COLORS[i],'interval':legend_labels[i]} for i in range(5)],
            'cutpoints_dbm':BOUNDARIES.tolist(),'boundary_rule':'left inclusive: -40 belongs to 较差; -35 一般; -30 较好; -25 极好',
            'model_source':'.','test_group':'P74','demo_task_index':task,'demo_budget':12,'demo_repeat':0,
            'evaluation':'hidden measured points only; input values copied for display; AP/missing excluded',
            'selected_predictor':'pathloss_gpr selected previously by validation; no retraining or selection based on grade metrics',
            'source_sha256':{str(p.relative_to(SOURCE)):hashlib.sha256(p.read_bytes()).hexdigest() for p in
                             [SOURCE/'neural/data/maps.npz',SOURCE/'neural/data/evaluation_masks.npz',SOURCE/'gpr/predictions.npz',SOURCE/'neural/predictions.npz']}}
(OUT/'grade_definition.json').write_text(json.dumps(definition,ensure_ascii=False,indent=2),encoding='utf-8')
main=[r for r in summary if (r['budget'],r['distribution'])==(12,'uniform')]
report='''# 五档信号热力图与预测对比

## 分档定义（本实验相对分级）

| 档位 | RSSI范围(dBm) |
|---|---|
| 极好 | ≥−25 |
| 较好 | −30≤RSSI<−25 |
| 一般 | −35≤RSSI<−30 |
| 较差 | −40≤RSSI<−35 |
| 极差 | <−40 |

用户确认用这些固定阈值突出房间内差异。这些档位不是通用Wi-Fi联网质量标准；例如−41 dBm被本实验称为“极差”，并不意味着真实联网质量差。RSSI不能单独确定吞吐、延迟或掉线。

实测图与所有预测图使用同一阈值与颜色，不能各自按分位数重新分档。P74红星为路由器位置，灰格缺测/AP不分档。没有更改原始数据，也没有重新训练模型。

## 主要图表

- `P74_measured_vs_predicted_five_levels.png`：实测与验证选中的路径损耗＋GPR预测并排，显示每格RSSI。黑圆点为本例12个观察点，紫框为隐藏点预测跨档。
- `P74_all_models_five_levels.png`：同一输入点下的实测与七种方法对比；三个网络的展示种子分别按验证RMSE选择，不按测试分档效果挑选。
- `P74_five_level_confusion.png`：IDW与路径损耗＋GPR在10个固定12点掩码上的混淆矩阵。

前两张图都使用事先固定的第0号均匀12点掩码，不能把图上的单样例一致率误认为全部掩码的平均表现。

## 全部10个掩码的分档指标（12个均匀输入点）

仅在隐藏实测点计算：每个掩码有50个隐藏点，10个掩码共500次预测。网络指标对3个训练种子取均值；±为种子标准差。未把已输入的实测值或AP格计入正确率。

| 方法 | 档位一致率 | 相差≤1档 | 平均绝对档位误差 | 预测偏乐观比例 | RSSI RMSE(dB) |
|---|---:|---:|---:|---:|---:|
'''
for row in main:
    accuracy=f'{row["exact_grade_agreement_pct"]:.1f}%'
    if row['training_seeds']>1:accuracy+=f' ± {row["exact_grade_agreement_pct_seed_std"]:.1f}%'
    report+=f'| {NAMES[row["model"]]} | {accuracy} | {row["within_one_grade_pct"]:.1f}% | {row["mean_abs_grade_error"]:.3f} | {row["overoptimistic_pct"]:.1f}% | {row["rmse_db"]:.3f} |\n'
report+='''
“一致率”是把RSSI回归结果按固定阈值离散化后的档位相同比例，并不是重新训练分类器得到的准确率。平均绝对档位误差=mean(|预测等级−实测等级|)，偏乐观表示预测等级高于实测等级。阈值附近很小的dB误差也会跨档，因此应同时看RSSI的MAE/RMSE，不能只凭颜色好看或档位准确率判断模型。格内数字仅为显示而保留1位小数；分档和指标使用未舍入的数值，阈值附近应以原始数值表为准。

`five_level_metrics.csv` 包含6/12/24个输入点、均匀/聚集两种分布的七种方法指标；另有macro-F1、二次加权Kappa、偏悲观比例和连续RSSI误差。`five_level_metrics_by_seed.csv` 保存各种子，`confusion_counts.csv` 保存实测档位×预测档位计数。网络的confusion按种子分别保存，不能合并后声称独立样本增加。

`P74_demo_per_point.csv` 包含主图63格的数值、档位与是否用于输入/评估，AP行留空，不填成0dBm。图中的输入格保留输入实测值，所有分档指标只算隐藏点。

本实验沿用剔除P26/P49后的8组方案：6组训练、P62验证、P74探索性测试。P74曾在以前实验中用于验证，且只覆盖一个房间部署，不能将结果当作新的盲测或跨房间泛化证明。固定掩码中的500次预测并非500个独立场景。
'''
(OUT/'五档热力图说明与指标.md').write_text(report,encoding='utf-8')
print(json.dumps({'output':str(OUT),'demo_score':case_score,'summary_12_uniform':main},ensure_ascii=False,indent=2))
