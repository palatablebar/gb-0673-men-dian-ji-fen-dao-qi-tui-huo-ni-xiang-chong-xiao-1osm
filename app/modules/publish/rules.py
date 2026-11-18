"""按会员粒度发布可用积分。

隔离行本身不参与记账，但被隔离的事件会让对应会员的余额变成未确定：
- 直接出现在隔离行里的会员；
- 被异常退款引用的原消费会员（退款一旦修正，原消费的已退金额会变化）。

这些会员本批停用，余额冻结并保留原因；其余会员的可用余额照常发布。
对账不通过时整批不发布，继续沿用上一版已发布余额。
"""
from app.modules.intake.rules import FIELDS

OK='可消费'
HOLD='停用'
REVIEW='待复核'
PUBLISHABLE='可发布'

def _recognized(row):
    """截断行只有前几列，第二格未必是会员号；只按字段完整的原始行认会员。"""
    return all(key in row for key in FIELDS) and bool((row.get('member') or '').strip())

def assess(state):
    ledger=state['point_ledger']
    books={}
    for lot in ledger['lots']:
        books[lot['member']]=books.get(lot['member'],0)+lot['remaining']
    members=set(books)
    for spend in ledger['spends']: members.add(spend['member'])
    for refund in ledger['refunds']: members.add(refund['member'])

    owners={spend['id']:spend['member'] for spend in ledger['spends']}
    evidence={member:[] for member in members}
    unattributed=[]
    for item in state['quarantine']:
        raw=item.get('raw') or {}
        row={'id':item.get('id',''),'code':item.get('code',''),'line':item.get('line',0),
             'detail':item.get('detail',''),'relation':'本户异常记录'}
        if not _recognized(raw):
            unattributed.append(row);continue
        member=raw['member'].strip()
        if member not in members:
            members.add(member);books.setdefault(member,0);evidence.setdefault(member,[])
        evidence[member].append(row)
        # 异常退款引用了另一会员的有效消费时，原消费会员的已退金额同样未确定。
        ref=(raw.get('ref') or '').strip()
        if raw.get('type')=='REFUND' and ref in owners and owners[ref]!=member:
            evidence.setdefault(owners[ref],[]).append({**row,'relation':'异常退款引用本户消费'})

    rows=[]
    for member in sorted(members):
        reasons=[]
        for item in evidence.get(member,[]):
            key=(item['line'],item['id'],item['code'],item['relation'])
            if key not in {(r['line'],r['id'],r['code'],r['relation']) for r in reasons}: reasons.append(item)
        affected=bool(reasons)
        book=books.get(member,0)
        rows.append({
            'member':member,
            'status':HOLD if affected else OK,
            'book_balance':book,
            'usable_balance':0 if affected else book,
            'frozen_balance':book if affected else 0,
            'reason_codes':','.join(sorted({r['code'] for r in reasons})),
            'evidence_ids':','.join(sorted({r['id'] for r in reasons if r['id']})),
            'reason':'；'.join(
                f"第{r['line']}行 {r['id'] or '—'}：{r['detail']}" if r['relation']=='本户异常记录'
                else f"第{r['line']}行 {r['id'] or '—'}：{r['relation']}（{r['detail']}）"
                for r in reasons) if reasons else '已采信，无隔离事件',
            'evidence':reasons,
        })

    blocked=[]
    balance=state.get('balance') or {}
    if not balance.get('row_balanced'):
        blocked.append({'code':'ROW_UNBALANCED','detail':'原始行数未守恒，本批余额不可发布'})
    if not balance.get('points_balanced'):
        blocked.append({'code':'POINTS_UNBALANCED','detail':'积分守恒未通过，本批余额不可发布'})
    if state.get('refund_issues'):
        blocked.append({'code':'REFUND_UNVERIFIED','detail':'退款审计未通过：'+','.join(state['refund_issues'])})
    publishable=not blocked
    summary={
        'members':len(rows),
        'available_members':sum(1 for r in rows if r['status']==OK),
        'suspended_members':sum(1 for r in rows if r['status']==HOLD),
        'book_points':sum(r['book_balance'] for r in rows),
        'usable_points':sum(r['usable_balance'] for r in rows),
        'frozen_points':sum(r['frozen_balance'] for r in rows),
    }
    return {
        'status':PUBLISHABLE if publishable else REVIEW,
        'publishable':publishable,
        'blocked':blocked,
        'members':rows,
        'summary':summary,
        'unattributed':unattributed,
        'rule':state['context'].get('as_of'),
    }

def apply(state):
    state['publication']=assess(state)
    pub=state['publication']
    state['views']['publish']={
        'title':'发布可用余额',
        'rows':[{k:v for k,v in row.items() if k!='evidence'} for row in pub['members']],
        'metrics':{'可消费户':pub['summary']['available_members'],
                   '停用户':pub['summary']['suspended_members'],
                   '可用积分':pub['summary']['usable_points'],
                   '冻结积分':pub['summary']['frozen_points'],
                   '状态':pub['status']},
    }
