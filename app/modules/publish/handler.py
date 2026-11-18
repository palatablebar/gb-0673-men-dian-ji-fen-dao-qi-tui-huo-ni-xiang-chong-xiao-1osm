from fastapi import APIRouter, Request, Body
from app.engine import evaluate
from app.web import render
from app import store
from app.errors import BusinessError
router=APIRouter()

def _db():
    from app.main import DB_PATH
    return DB_PATH

def _load(payload):
    """取待发布批次：run_id 优先；否则按当前参数计算并保存一个批次。"""
    run_id=payload.get('run_id')
    if run_id:
        if not isinstance(run_id,str): raise BusinessError('BAD_RUN_ID','批次编号无效')
        return store.get(_db(),run_id)
    return store.save(_db(),evaluate(payload))

def _publish(result):
    if 'publication' not in result:
        raise BusinessError('PUBLICATION_MISSING','批次缺少发布结果，请重跑该批')
    return store.publish(_db(),result)

@router.get('/publish')
def page(request:Request):
    run_id=request.query_params.get('run_id')
    if run_id:
        state=store.get(_db(),run_id)
    else:
        state=store.save(_db(),evaluate({'dataset':request.query_params.get('dataset','dirty')}))
    if 'publication' not in state:
        raise BusinessError('PUBLICATION_MISSING','批次缺少发布结果，请重跑该批')
    active=store.active(_db())
    digest=store.publication_digest(state)
    stored=store.publication_by_digest(_db(),digest) if digest else None
    return render(request,'publish',{'active':active,'published':stored,'lock_source':True})

@router.post('/api/publish')
def calculate(payload:dict=Body(default={})):
    return _publish(_load(payload))

@router.post('/api/runs/{run_id}/publish')
def publish_stored_run(run_id:str):
    return _publish(store.get(_db(),run_id))

@router.get('/api/publications/active')
def active_publication():
    publication=store.active(_db())
    if publication is None: raise BusinessError('NOT_PUBLISHED','尚无已发布余额',404)
    return publication

@router.get('/api/publications')
def publications():
    return store.publications(_db())
