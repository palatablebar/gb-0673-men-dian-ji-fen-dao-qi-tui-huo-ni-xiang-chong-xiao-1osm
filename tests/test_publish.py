from copy import deepcopy
from fastapi.testclient import TestClient
from app import main, store
from app.engine import evaluate
from app.modules.publish.rules import assess

def _member(pub,member):
    return next(x for x in pub['members'] if x['member']==member)

def _client(tmp_path,monkeypatch):
    monkeypatch.setattr(main,'DB_PATH',str(tmp_path/'publish.sqlite'))
    return TestClient(main.app)

def test_unaffected_members_stay_usable(sample):
    pub=sample['publication']
    assert pub['publishable'] and pub['status']=='可发布'
    assert pub['summary']=={'members':15,'available_members':12,'suspended_members':3,
        'book_points':1010,'usable_points':840,'frozen_points':170}

def test_suspended_members_keep_book_and_reason(sample):
    pub=sample['publication']
    for member in ('00001','00002','00003'):
        row=_member(pub,member)
        assert row['status']=='停用' and row['usable_balance']==0
        assert row['frozen_balance']==row['book_balance']
        assert row['reason_codes'] and row['evidence_ids'] and '第' in row['reason']
    assert _member(pub,'00001')['reason_codes']=='REFUND_CAP,REFUND_REFERENCE'
    assert _member(pub,'00002')['reason_codes']=='INSUFFICIENT_POINTS,MISSING_VALUE,REFUND_REFERENCE'

def test_clean_batch_all_usable(clean):
    pub=clean['publication']
    assert pub['summary']['suspended_members']==0
    assert pub['summary']['usable_points']==pub['summary']['book_points']==1010

def test_unrecognised_row_is_not_a_member(sample):
    pub=sample['publication']
    assert [x['id'] for x in pub['unattributed']]==['BADCOL']
    assert len(pub['members'])==15

def _csv(rows):
    header='id,member,type,points,at,expires,ref,rule,version'
    return header+'\n'+'\n'.join(rows)+'\n'

def test_dependency_on_other_members_spend():
    # 00002 的消费 S9 完全正常；00009 的退款错误引用它，一旦修正 00002 的已退金额随之变化，故 00002 一并停用。
    rows=[
      'E9,00002,EARN,100,2026-10-01T09:00:00+08:00,2026-12-01T00:00:00+08:00,,常规,1',
      'S9,00002,SPEND,40,2026-10-15T09:00:00+08:00,,,常规,1',
      'E20,00009,EARN,10,2026-10-01T09:00:00+08:00,2026-12-01T00:00:00+08:00,,常规,1',
      'BAD9,00009,REFUND,10,2026-10-22T09:00:00+08:00,,S9,常规,1',
    ]
    pub=assess(evaluate({'csv_text':_csv(rows)}))
    assert _member(pub,'00009')['status']=='停用'
    owner=_member(pub,'00002')
    assert owner['status']=='停用' and owner['usable_balance']==0
    assert '异常退款引用本户消费' in owner['reason']

def test_bad_refund_does_not_touch_unrelated_members():
    rows=[
      'E21,00021,EARN,100,2026-10-01T09:00:00+08:00,2026-12-01T00:00:00+08:00,,常规,1',
      'E22,00022,EARN,100,2026-10-01T09:00:00+08:00,2026-12-01T00:00:00+08:00,,常规,1',
      'BAD22,00022,REFUND,10,2026-10-22T09:00:00+08:00,,S-missing,常规,1',
    ]
    pub=assess(evaluate({'csv_text':_csv(rows)}))
    assert _member(pub,'00022')['status']=='停用'
    assert _member(pub,'00021')['usable_balance']==100

def test_publish_is_repeatable_without_double_freeze(sample,tmp_path):
    path=str(tmp_path/'pub.sqlite')
    first=store.publish(path,sample)
    assert first['published'] and first['version']==1
    assert first['summary']['frozen_points']==170
    again=store.publish(path,sample)
    assert again['publication_id']==first['publication_id'] and again['version']==1
    assert '未重复冻结' in again['message']
    assert len(store.publications(path))==1
    assert store.active(path)['publication_id']==first['publication_id']

def test_blocked_publication_keeps_previous(clean,tmp_path):
    path=str(tmp_path/'pub.sqlite')
    v1=store.publish(path,clean)
    broken=deepcopy(clean)
    broken['publication']=dict(broken['publication'],publishable=False,status='待复核',
        blocked=[{'code':'POINTS_UNBALANCED','detail':'积分守恒未通过，本批余额不可发布'}])
    refused=store.publish(path,broken)
    assert refused['published'] is False and refused['blocked'][0]['code']=='POINTS_UNBALANCED'
    assert refused['version']==v1['version']
    assert store.active(path)['publication_id']==v1['publication_id']
    assert len(store.publications(path))==1

def test_actions_api_publish_and_rerun_idempotent(tmp_path,monkeypatch):
    c=_client(tmp_path,monkeypatch)
    with c:
        run=c.post('/api/runs',json={}).json(); rid=run['manifest']['run_id']
        one=c.post('/api/runs/'+rid+'/publish').json()
        assert one['published'] and one['summary']['suspended_members']==3
        assert c.get('/api/publications/active').json()['publication_id']==one['publication_id']
        c.post('/api/runs/'+rid+'/rerun')
        two=c.post('/api/runs/'+rid+'/publish').json()
        assert two['publication_id']==one['publication_id'] and two['version']==one['version']
        assert len(c.get('/api/publications').json())==1

def test_publish_page_shows_each_member_status(tmp_path,monkeypatch):
    c=_client(tmp_path,monkeypatch)
    with c:
        run=c.post('/api/runs',json={}).json(); rid=run['manifest']['run_id']
        html=c.get('/publish?run_id='+rid).text
        for member in ('00001','00002','00003'):
            assert member in html
        assert '停用依据' in html and 'REFUND_CAP' in html and '可用余额' in html
        # 发布页只处理本批，不展开样例切换或其他批次。
        assert 'id="dataset"' not in html
        assert html.count('停用依据')==1

def test_active_publication_missing_is_404(tmp_path,monkeypatch):
    c=_client(tmp_path,monkeypatch)
    with c:
        assert c.get('/api/publications/active').status_code==404

def test_publish_unknown_run_404(tmp_path,monkeypatch):
    c=_client(tmp_path,monkeypatch)
    with c:
        assert c.post('/api/runs/nope/publish').status_code==404

def _broken_run(sample,run_id='brokenrun1'):
    from app.modules.reconcile.rules import apply as reconcile_apply
    from app.modules.archive.rules import apply as archive_apply
    from app.modules.publish.rules import apply as publish_apply
    broken=deepcopy(sample)
    broken['point_ledger']['lots'][0]['remaining']+=1
    broken['manifest']=dict(broken['manifest'],run_id=run_id,sha256='0'*64)
    broken['views']={}
    reconcile_apply(broken);archive_apply(broken);publish_apply(broken)
    return broken

def test_unbalanced_batch_does_not_replace_active(sample,tmp_path):
    path=str(tmp_path/'pub.sqlite')
    good=store.publish(path,sample)
    broken=_broken_run(sample)
    assert broken['publication']['publishable'] is False
    assert broken['publication']['blocked'][0]['code']=='POINTS_UNBALANCED'
    refused=store.publish(path,broken)
    assert refused['published'] is False and refused['version']==good['version']
    assert store.active(path)['publication_id']==good['publication_id']
    assert len(store.publications(path))==1

def test_unbalanced_api_and_page_keep_old_balance(sample,tmp_path,monkeypatch):
    c=_client(tmp_path,monkeypatch)
    with c:
        run=c.post('/api/runs',json={}).json(); rid=run['manifest']['run_id']
        first=c.post('/api/runs/'+rid+'/publish').json()
        broken=_broken_run(sample)
        store.save(main.DB_PATH,broken)
        refused=c.post('/api/runs/brokenrun1/publish')
        assert refused.status_code==200
        assert refused.json()['published'] is False and refused.json()['version']==first['version']
        assert c.get('/api/publications/active').json()['publication_id']==first['publication_id']
        html=c.get('/publish?run_id=brokenrun1').text
        assert 'POINTS_UNBALANCED' in html and '对账未通过' in html
        assert 'v%d'%first['version'] in html

def test_repeat_publish_does_not_add_ledger_entries(sample,tmp_path):
    path=str(tmp_path/'pub.sqlite')
    once=store.publish(path,sample)
    before=store.active(path)
    twice=store.publish(path,sample)
    after=store.active(path)
    assert before['summary']==after['summary']==twice['summary']
    assert before['members']==after['members']==twice['members']
    assert len(store.publications(path))==1

def test_concurrent_publish_creates_one_version(sample,tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    path=str(tmp_path/'pub.sqlite')
    with ThreadPoolExecutor(max_workers=6) as pool:
        results=list(pool.map(lambda _: store.publish(path,sample),range(6)))
    assert len(store.publications(path))==1
    assert {r['publication_id'] for r in results}=={store.active(path)['publication_id']}
    assert all(r['published'] for r in results)
