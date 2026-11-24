import pytest
from app.blend.changeover import approve
from app.job import MixingJob
from app.receipts.contract import Workshop
from app.receipts.packet import read_sample
from app.receipts.tickets import eligible


@pytest.fixture(scope='module')
def wash():
    return MixingJob(read_sample('wash'))


def wash_row(ticket,pot,at,net,before,after,parts,delay=False):
    return {'data':{'ticket':ticket,'pot':pot,'action':'WASH'},'at':at,'line':1,'received_utc':at,
            'delay':delay,'net_mg':net,'before_mg':before,'after_mg':after,'parts_mg':parts}


def apply_row(ticket,pot,at,net,parts):
    return {'data':{'ticket':ticket,'pot':pot,'action':'APPLY'},'at':at,'line':1,'received_utc':at,
            'delay':False,'net_mg':net,'parts_mg':parts}


def ticket_row(ticket,pot,action,gross,received,line):
    return {'sheet':'weights','line':line,'raw':[],
            'data':{'ticket':ticket,'pot':pot,'container':'C','action':action,'lot':'','gross':gross,
                    'unit':'g','occurred':'2026-11-08T10:00:00+08:00','received':received}}


def test_partial_pour_shortfall_stays_on_old_color(wash):
    # 洗罐票报清空但实称只倒出一部分：差额留在旧色账上
    change=next(c for c in wash.changeover['changes'] if c['ticket']=='0100')
    assert (change['status'],change['before_mg'],change['poured_mg'],change['shortfall_mg'])==('CLEARED',60000,50000,10000)
    assert change['parts_mg']=={'R-017':50000}
    assert wash.recovery['pending_mg']['T20']=={'R-017':15000}


def test_new_color_apply_held_until_reweigh(wash):
    # 差额未清、复秤未到：新色上件暂不放行
    gate=next(g for g in wash.changeover['gates'] if g['ticket']=='0902')
    assert gate['status']=='HELD'
    assert wash.changeover['totals']['held_mg']==10000


def test_late_reweigh_releases_only_post_wash_applies(wash):
    # 晚到的复秤记录证实洗净：只放行实际洗净后做的上件，早先串色的仍留异常
    confirm=next(c for c in wash.changeover['changes'] if c['ticket']=='0101')
    assert (confirm['status'],confirm['delay'],confirm['shortfall_mg'])==('CONFIRMED',True,0)
    gates={g['ticket']:g['status'] for g in wash.changeover['gates']}
    assert gates['0103']=='TAINTED' and gates['0105']=='RELEASED'
    tainted=next(g for g in wash.changeover['gates'] if g['ticket']=='0103')
    assert tainted['parts_mg']=={'R-017':5000,'B-003':40000}


def test_clean_wash_releases_without_waiting(wash):
    # 罐已洗净不等整班复秤：差额为零的洗罐票之后上件立即放行
    change=next(c for c in wash.changeover['changes'] if c['ticket']=='0200')
    assert (change['status'],change['shortfall_mg'])==('VERIFIED',0)
    gate=next(g for g in wash.changeover['gates'] if g['ticket']=='0204')
    assert gate['status']=='RELEASED'


def test_same_wash_ticket_two_shifts_registers_once(wash):
    # 两班同时提交同一张洗罐票办不同换色：入册的换色和对应损耗都只能有一份
    booked=[c for c in wash.changeover['changes'] if c['ticket']=='0900']
    assert len(booked)==1 and booked[0]['pot']=='T20' and booked[0]['poured_mg']==15000
    conflict=[r for r in wash.changeover['failed'] if r['code']=='WASH_CONFLICT']
    assert len(conflict)==1 and conflict[0]['data']['pot']=='T21'
    r017=next(r for r in wash.balance['lines'] if r['lot']=='R-017')
    assert r017['loss_mg']==70000


def test_changeover_totals_and_mass_conservation(wash):
    assert wash.changeover['totals']=={'changes':4,'wash_loss_mg':150000,'pending_shortfall_mg':25000,
                                       'released_mg':60000,'held_mg':10000,'tainted_mg':45000}
    assert all(r['check_mg']==0 for r in wash.balance['lines'])
    assert wash.balance['difference_lines']==0


def test_trial_api_and_desk_page(client):
    response=client.get('/api/trial/wash/changeover')
    assert response.status_code==200 and response.json['totals']['tainted_mg']==45000
    page=client.get('/studio/changeover?sample=wash')
    assert page.status_code==200 and '换色核准' in page.get_data(as_text=True)


def test_changeover_in_archived_snapshot(client):
    key=client.post('/api/books',json={'sample':'wash'}).json['key']
    record=client.get('/api/export/'+key).json
    assert [c['ticket'] for c in record['snapshot']['changeover']['changes']]==['0100','0200','0900','0101']


def test_second_changeover_round_reopens_gate():
    rows=[wash_row('w1','P','t1',100,{'R':100},{'R':100},{'R':100}),
          apply_row('a1','P','t2',50,{'R':50}),
          wash_row('w2','P','t3',100,{'R':100},{},{'R':100}),
          apply_row('a2','P','t4',50,{'B':50}),
          wash_row('w3','P','t5',60,{'B':100},{'B':40},{'B':60}),
          apply_row('a3','P','t6',40,{'B':40})]
    result=approve(rows,[])
    assert [c['status'] for c in result['changes']]==['CLEARED','CONFIRMED','SHORTFALL']
    assert {g['ticket']:g['status'] for g in result['gates']}=={'a1':'TAINTED','a2':'RELEASED','a3':'HELD'}


def test_empty_pot_wash_is_verified_immediately():
    rows=[wash_row('w1','P','t1',0,{},{},{})]
    assert approve(rows,[])['changes'][0]['status']=='VERIFIED'


def test_identical_wash_ticket_is_single_duplicate():
    job=Workshop.parse({'order':'T','start':'2026-11-08T08:00:00+08:00',
                        'end':'2026-11-08T18:00:00+08:00','cutoff':'2026-11-08T22:00:00+08:00'})
    pair=[ticket_row('01','P1','WASH','10','2026-11-08T10:01:00+08:00',2),
          ticket_row('01','P1','WASH','10','2026-11-08T10:02:00+08:00',3)]
    accepted,rejected=eligible(pair,job)
    assert len(accepted)==1 and [r['code'] for r in rejected]==['TICKET_DUPLICATE']


def test_wash_conflicting_with_other_action_stays_isolated():
    job=Workshop.parse({'order':'T','start':'2026-11-08T08:00:00+08:00',
                        'end':'2026-11-08T18:00:00+08:00','cutoff':'2026-11-08T22:00:00+08:00'})
    pair=[ticket_row('01','P1','WASH','10','2026-11-08T10:01:00+08:00',2),
          ticket_row('01','P1','APPLY','10','2026-11-08T10:02:00+08:00',3)]
    accepted,rejected=eligible(pair,job)
    assert not accepted and {r['code'] for r in rejected}=={'TICKET_CONFLICT'}


def test_overdrawn_wash_ticket_isolated_not_booked():
    files=read_sample('wash')
    files['weights']+='0999,T20,TRAY,WASH,,999,g,2026-11-08T12:00:00+08:00,2026-11-08T12:00:00+08:00\n'
    job=MixingJob(files)
    failed=[r for r in job.changeover['failed'] if r['data']['ticket']=='0999']
    assert [r['code'] for r in failed]==['OVERDRAW']
    assert not any(c['ticket']=='0999' for c in job.changeover['changes'])
