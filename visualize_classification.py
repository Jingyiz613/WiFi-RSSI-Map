from pathlib import Path
import json
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap,BoundaryNorm

def main():
    root=Path(__file__).resolve().parent
    out=root/'output/classification_runs/20261022_135426_063941'
    z=np.load(out/'validation_display.npz')
    plt.rcParams.update({'font.sans-serif':['Microsoft YaHei','SimHei','DejaVu Sans'],'axes.unicode_minus':False})
    cmap=ListedColormap(['#64569b','#4c9abe','#e0c85a','#e79645','#d15c53']);cmap.set_bad('#e8ebef')
    norm=BoundaryNorm(np.arange(-.5,5.5),5)
    truth=z['truth'].astype(float);hidden=z['hidden_mask'];observed=z['input_mask'].astype(bool)
    inp=np.where(observed,truth,np.nan)
    actual=np.where(hidden|observed,truth,np.nan)
    model=np.where(hidden,z['probabilities'].argmax(0),np.nan)
    base=np.where(hidden,z['baseline'],np.nan)
    fig,axes=plt.subplots(2,2,figsize=(12,10),layout='constrained')
    for ax,data,title in zip(axes.flat,[inp,actual,model,base],['20个输入测点的等级','62个实测点的参考等级','分类网络预测（42个隐藏点）','距离估算分档（同样42点）']):
        im=ax.imshow(data,origin='lower',extent=[0,8.23,0,6.95],cmap=cmap,norm=norm,interpolation='nearest',aspect='equal')
        ax.set_title(title);ax.set_xlabel('x / m');ax.set_ylabel('y / m')
        ax.scatter((4-.5)*8.23/9,(6-.5)*6.95/7,marker='*',s=130,c='black',edgecolor='white')
    cb=fig.colorbar(im,ax=axes.ravel().tolist(),ticks=range(5),shrink=.75)
    cb.ax.set_yticklabels(['弱 <−40','较弱 [−40,−35)','中 [−35,−30)','较强 [−30,−25)','强 ≥−25'])
    cb.set_label('项目相对等级 / RSSI阈值 dBm')
    fig.suptitle('P64｜五档信号分类展示',fontsize=19)
    fig.supxlabel('固定第1组选点，不挑最好案例。灰色＝本图不展示值；星号＝路由器。\n阈值仅表示本项目相对强弱，不代表通用Wi-Fi质量；P64用于模型选择，并非独立测试。',fontsize=10)
    fig.savefig(out/'五档分类展示_P64.png',dpi=170);plt.close(fig)
    s=json.loads((out/'summary.json').read_text(encoding='utf-8'))
    fig,axes=plt.subplots(1,2,figsize=(12,5),layout='constrained')
    for ax,key,title in zip(axes,['classification_model','idw_binned_baseline'],['分类网络','距离估算分档']):
        cm=np.array(s[key]['confusion_matrix']);im=ax.imshow(cm,cmap='Blues')
        for i in range(5):
            for j in range(5):ax.text(j,i,str(cm[i,j]),ha='center',va='center',color='white' if cm[i,j]>cm.max()/2 else 'black')
        ax.set_xticks(range(5),['弱','较弱','中','较强','强']);ax.set_yticks(range(5),['弱','较弱','中','较强','强'])
        ax.set_xlabel('预测等级');ax.set_ylabel('真实等级');ax.set_title(title)
    fig.suptitle('P64 验证混淆矩阵｜对角线为分类正确',fontsize=16)
    fig.supxlabel('10种固定选点共420次评分，仍只有62个实测位置；数字不是420个独立测点。',fontsize=10)
    fig.savefig(out/'五档分类混淆矩阵.png',dpi=170);plt.close(fig)
    print('Saved five-color maps and confusion matrices:',out)

if __name__=='__main__':main()
