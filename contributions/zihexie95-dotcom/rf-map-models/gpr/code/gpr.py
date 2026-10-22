"""Small NumPy-only Gaussian process/ordinary Kriging predictor, in metres and dBm."""
import numpy as np

ROWS, COLS = 7, 9
WIDTH, HEIGHT = 8.23, 6.95
yy, xx = np.meshgrid((np.arange(ROWS)+.5)*HEIGHT/ROWS,
                     (np.arange(COLS)+.5)*WIDTH/COLS, indexing='ij')
XY = np.column_stack([xx.ravel(), yy.ravel()])

def ap_position(point):
    if len(point)!=3 or point[0]!='P' or not point[1:].isdigit():
        raise ValueError('AP must be a grid label such as P49')
    r,c=int(point[1])-1,int(point[2])-1
    if not (0<=r<ROWS and 0<=c<COLS):raise ValueError(point)
    return np.array([(c+.5)*WIDTH/COLS,(r+.5)*HEIGHT/ROWS])

def path_mean(points, ap, coefficients):
    distance=np.maximum(np.linalg.norm(points-np.asarray(ap),axis=1),.5)
    return coefficients[0]+coefficients[1]*np.log10(distance)

def covariance(a,b,parameters):
    distance=np.linalg.norm(a[:,None,:]-b[None,:,:],axis=2)/parameters['length_m']
    if parameters['nu']==1.5:
        z=np.sqrt(3)*distance; correlation=(1+z)*np.exp(-z)
    elif parameters['nu']==2.5:
        z=np.sqrt(5)*distance; correlation=(1+z+z*z/3)*np.exp(-z)
    else:raise ValueError('Only Matern 1.5 and 2.5 are supported')
    return parameters['signal_db']**2*correlation

def factor(points,parameters):
    matrix=covariance(points,points,parameters)
    matrix.flat[::len(points)+1]+=parameters['noise_db']**2+1e-8
    return np.linalg.cholesky(matrix)

def solve(cholesky,values):
    return np.linalg.solve(cholesky.T,np.linalg.solve(cholesky,values))

def training_reml(points,values,parameters,trend):
    """Restricted negative log likelihood with a separately fitted scene intercept."""
    residual=np.asarray(values)-trend; cholesky=factor(points,parameters)
    ones=np.ones(len(values)); precision=solve(cholesky,np.column_stack([residual,ones]))
    information=ones@precision[:,1]
    intercept=(ones@precision[:,0])/information
    centered=residual-intercept
    alpha=precision[:,0]-intercept*precision[:,1]
    return float(.5*(centered@alpha+2*np.log(np.diag(cholesky)).sum()
                      +np.log(information)+(len(values)-1)*np.log(2*np.pi)))

def predict(model,observed_xy,observed_values,query_xy,ap=None):
    observed_xy=np.asarray(observed_xy,dtype=float)
    values=np.asarray(observed_values,dtype=float)
    query_xy=np.asarray(query_xy,dtype=float)
    if len(values)==0 or observed_xy.shape!=(len(values),2):raise ValueError('Invalid observations')
    if not np.isfinite(values).all() or not np.isfinite(observed_xy).all():raise ValueError('Non-finite observations')
    if len(np.unique(observed_xy,axis=0))!=len(values):raise ValueError('Duplicate observation locations')
    kind=model['kind']
    if kind=='idw':
        squared=((query_xy[:,None,:]-observed_xy[None,:,:])**2).sum(2)
        weights=1/(squared+.15**2)
        return weights@values/weights.sum(1),None
    if kind in ['pathloss','pathloss_gpr']:
        if ap is None:raise ValueError('Path-loss methods require the known AP position')
        training_trend=path_mean(observed_xy,ap,model['pathloss_coefficients'])
        query_trend=path_mean(query_xy,ap,model['pathloss_coefficients'])
    else:training_trend=np.zeros(len(values));query_trend=np.zeros(len(query_xy))
    residual=values-training_trend
    if kind=='pathloss':
        return query_trend+residual.mean(),None
    parameters=model['kernel'];cholesky=factor(observed_xy,parameters)
    ones=np.ones(len(values))
    precision=solve(cholesky,np.column_stack([residual,ones]))
    information=ones@precision[:,1]
    intercept=(ones@precision[:,0])/information
    cross=covariance(query_xy,observed_xy,parameters)
    prediction=query_trend+intercept+cross@(precision[:,0]-intercept*precision[:,1])
    # Universal Kriging variance includes uncertainty in the local intercept.
    projected=np.linalg.solve(cholesky,cross.T)
    variance=parameters['signal_db']**2-(projected**2).sum(0)
    variance+=(1-cross@precision[:,1])**2/information
    # Interval is for a held-out measured point target, including its independent nugget.
    variance+=parameters['noise_db']**2
    return prediction,np.maximum(variance,0.)

def self_check():
    p={'length_m':1.5,'signal_db':6.,'noise_db':.5,'nu':1.5}
    model={'kind':'gpr','kernel':p}
    xy=XY[[0,8,31,62]];values=np.array([-40.,-44.,-32.,-38.])
    a,var=predict(model,xy,values,XY)
    b,_=predict(model,xy[::-1],values[::-1],XY)
    assert np.allclose(a,b,atol=1e-9)
    constant,_=predict(model,xy,np.full(4,-42.),XY)
    assert np.allclose(constant,-42.) and np.isfinite(var).all() and (var>=0).all()
    nearly_exact={'kind':'gpr','kernel':{**p,'noise_db':1e-6}}
    recovered,_=predict(nearly_exact,xy,values,xy)
    assert np.allclose(recovered,values,atol=1e-6)
    print('PASS: permutation invariance, constant observations, finite uncertainty, near-noiseless interpolation.')

if __name__=='__main__':self_check()
