import sqlite3, json, hashlib
from datetime import datetime, timezone
from pathlib import Path
from app.errors import BusinessError

def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con=sqlite3.connect(path,timeout=15)
    con.row_factory=sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    con.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, digest TEXT UNIQUE NOT NULL, result TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT '待复核')")
    con.execute("CREATE TABLE IF NOT EXISTS publications (id TEXT PRIMARY KEY, run_id TEXT NOT NULL, version INTEGER NOT NULL, status TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 0, result TEXT NOT NULL, created_at TEXT NOT NULL)")
    con.execute('CREATE INDEX IF NOT EXISTS publications_run ON publications(run_id)')
    con.execute('CREATE UNIQUE INDEX IF NOT EXISTS publications_version ON publications(version)')
    return con

def save(path, result):
    run_id=result['manifest']['run_id']
    with connect(path) as con:
        con.execute('INSERT OR IGNORE INTO runs(id,digest,result) VALUES(?,?,?)', (run_id,result['manifest']['sha256'],json.dumps(result,ensure_ascii=False)))
    return get(path,run_id)

def get(path, run_id):
    with connect(path) as con: row=con.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
    if not row: raise BusinessError('NOT_FOUND','批次不存在',404)
    result=json.loads(row['result']);result.update(revision=row['revision'],status=row['status'])
    return result

def history(path):
    with connect(path) as con:
        return [dict(r) for r in con.execute('SELECT id,revision,status FROM runs ORDER BY rowid DESC LIMIT 100')]

def review(path,run_id,revision):
    with connect(path) as con:
        cur=con.execute('UPDATE runs SET revision=revision+1,status=? WHERE id=? AND revision=?',('已复核',run_id,revision))
        if cur.rowcount!=1:
            if not con.execute('SELECT 1 FROM runs WHERE id=?',(run_id,)).fetchone():
                raise BusinessError('NOT_FOUND','批次不存在',404)
            raise BusinessError('STALE_VERSION','复核版本已更新，请刷新',409)
    return get(path,run_id)

def _publication_digest(run_id, publication):
    """发布内容的稳定指纹：同批同结果只生成一个发布版本，重跑不会重复冻结。"""
    body={'run_id':run_id,'status':publication['status'],'blocked':publication['blocked'],
          'summary':publication['summary'],'rule':publication.get('rule'),
          'members':[{k:v for k,v in row.items() if k!='evidence'} for row in publication['members']]}
    return hashlib.sha256(json.dumps(body,ensure_ascii=False,sort_keys=True).encode()).hexdigest()[:16]

def _publication_row(path, publication_id):
    with connect(path) as con:
        row=con.execute('SELECT * FROM publications WHERE id=?',(publication_id,)).fetchone()
    if not row: raise BusinessError('NOT_FOUND','发布版本不存在',404)
    result=json.loads(row['result'])
    result.update(publication_id=row['id'],version=row['version'],run_id=row['run_id'],
                  status=row['status'],active=bool(row['active']),published=True)
    return result

def _active(path):
    with connect(path) as con:
        row=con.execute('SELECT * FROM publications WHERE active=1 ORDER BY version DESC LIMIT 1').fetchone()
    if not row: return None
    result=json.loads(row['result'])
    result.update(publication_id=row['id'],version=row['version'],run_id=row['run_id'],
                  status=row['status'],active=True,published=True)
    return result

def active(path):
    return _active(path)

def publication_digest(result):
    """批次当前结果对应的发布编号；未可发布时没有编号。"""
    if 'publication' not in result or not result['publication']['publishable']: return None
    return _publication_digest(result['manifest']['run_id'],result['publication'])

def publication_by_digest(path,publication_id):
    try: return _publication_row(path,publication_id)
    except BusinessError: return None

def publications(path):
    with connect(path) as con:
        return [{'publication_id':r['id'],'run_id':r['run_id'],'version':r['version'],'status':r['status'],
                 'active':bool(r['active']),'created_at':r['created_at']}
                for r in con.execute('SELECT * FROM publications ORDER BY version DESC LIMIT 100')]

def publish(path, result):
    """发布一个批次的可用余额；对账失败时只返回阻断原因，不改动当前生效版本。"""
    if 'publication' not in result:
        raise BusinessError('PUBLICATION_MISSING','批次缺少发布结果，请重跑该批')
    run_id=result['manifest']['run_id']
    publication=result['publication']
    con=connect(path)
    try:
        # 写锁内完成读-判-写，并发发布不会重复冻结或拿到同一版本号。
        con.execute('BEGIN IMMEDIATE')
        previous=_active_with(con)
        if not publication['publishable']:
            con.rollback()
            return {'published':False,'status':publication['status'],'run_id':run_id,'publication_id':None,
                    'version':previous['version'] if previous else 0,
                    'blocked':publication['blocked'],'summary':publication['summary'],
                    'members':[{k:v for k,v in row.items() if k!='evidence'} for row in publication['members']],
                    'unattributed':publication['unattributed'],'previous':previous,
                    'message':'对账未通过，本批未发布，继续沿用上一版已发布余额'}
        publication_id=_publication_digest(run_id,publication)
        row=con.execute('SELECT * FROM publications WHERE id=?',(publication_id,)).fetchone()
        if row is not None:
            if not row['active']:
                # 明确再次发布曾被取代的结果：复用原版本恢复生效，不生成新的冻结或记账。
                con.execute('UPDATE publications SET active=0 WHERE active=1')
                con.execute('UPDATE publications SET active=1 WHERE id=?',(publication_id,))
                message='已重新启用既有发布版本 v%d，未重复冻结或记账'%row['version']
            else:
                message='该结果已发布过，沿用既有版本，未重复冻结或记账'
            con.commit()
            body=json.loads(row['result'])
            body.update(publication_id=row['id'],version=row['version'],run_id=row['run_id'],
                        status=row['status'],active=True,published=True,message=message)
            return body
        con.execute('UPDATE publications SET active=0 WHERE active=1')
        version=con.execute('SELECT COALESCE(MAX(version),0)+1 FROM publications').fetchone()[0]
        created_at=datetime.now(timezone.utc).isoformat()
        stored={**publication,'previous_publication_id':previous['publication_id'] if previous else None}
        con.execute('INSERT INTO publications(id,run_id,version,status,active,result,created_at) VALUES(?,?,?,?,?,?,?)',
                    (publication_id,run_id,version,publication['status'],1,json.dumps(stored,ensure_ascii=False),created_at))
        con.commit()
        return {**stored,'publication_id':publication_id,'version':version,'active':True,'published':True,
                'created_at':created_at,'previous':previous,'run_id':run_id,'status':publication['status'],
                'blocked':publication['blocked'],'summary':publication['summary'],
                'members':[{k:v for k,v in r.items() if k!='evidence'} for r in publication['members']],
                'unattributed':publication['unattributed'],
                'message':'已发布 v%d：可消费 %d 户，停用 %d 户'%(version,publication['summary']['available_members'],publication['summary']['suspended_members'])}
    finally:
        con.close()

def _active_with(con):
    row=con.execute('SELECT * FROM publications WHERE active=1 ORDER BY version DESC LIMIT 1').fetchone()
    if not row: return None
    result=json.loads(row['result'])
    result.update(publication_id=row['id'],version=row['version'],run_id=row['run_id'],
                  status=row['status'],active=True,published=True)
    return result
