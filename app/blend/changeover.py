from collections import defaultdict

# 换色核准状态
# VERIFIED   洗净核准：洗罐票实称倒出与账面存量一致，罐即刻洗净
# SHORTFALL  差额待复秤：报清空但实称只倒出一部分，差额留在旧色账上
# CLEARED    差额复秤结清：待复秤的差额由迟到的复秤记录倒出结清
# CONFIRMED  复秤证实洗净：迟到的复秤记录把差额清零，洗净时刻自该票起算
# RELEASED   放行：实际洗净后做的上件
# HELD       暂不放行：差额未清、复秤未到的新色上件
# TAINTED    串色异常：实际洗净时刻之前做的新色上件，仍留异常


def approve(rows, failed):
    by_pot=defaultdict(list)
    for row in rows:
        by_pot[row['data']['pot']].append(row)
    changes,gates=[],[]
    for pot in sorted(by_pot):
        clean_at=None
        pending=[]
        held=[]
        started=False
        for row in by_pot[pot]:
            fields=row['data']
            action=fields['action']
            if action=='WASH':
                started=True
                shortfall=sum(row['after_mg'].values())
                if shortfall:
                    status,clean_at='SHORTFALL',None
                else:
                    status='CONFIRMED' if pending else 'VERIFIED'
                    for entry in pending:
                        entry['status']='CLEARED'
                    pending.clear()
                    clean_at=row['at']
                    for gate in held:
                        gate['status']='RELEASED' if gate['at']>=clean_at else 'TAINTED'
                    held.clear()
                entry={'ticket':fields['ticket'],'pot':pot,'at':row['at'],'line':row['line'],
                       'received_utc':row['received_utc'],'delay':row['delay'],
                       'before_mg':sum(row['before_mg'].values()),'poured_mg':row['net_mg'],
                       'shortfall_mg':shortfall,'parts_mg':row['parts_mg'],'status':status}
                changes.append(entry)
                if status=='SHORTFALL':
                    pending.append(entry)
            elif action=='APPLY' and started:
                gate={'ticket':fields['ticket'],'pot':pot,'at':row['at'],'line':row['line'],
                      'net_mg':row['net_mg'],'parts_mg':row['parts_mg'],'status':'HELD'}
                if clean_at is None:
                    held.append(gate)
                elif row['at']>=clean_at:
                    gate['status']='RELEASED'
                else:
                    gate['status']='TAINTED'
                gates.append(gate)
    changes.sort(key=lambda c:(c['at'],c['ticket']))
    gates.sort(key=lambda g:(g['at'],g['ticket']))
    totals={'changes':len(changes),
            'wash_loss_mg':sum(c['poured_mg'] for c in changes),
            'pending_shortfall_mg':sum(c['shortfall_mg'] for c in changes if c['status']=='SHORTFALL'),
            'released_mg':sum(g['net_mg'] for g in gates if g['status']=='RELEASED'),
            'held_mg':sum(g['net_mg'] for g in gates if g['status']=='HELD'),
            'tainted_mg':sum(g['net_mg'] for g in gates if g['status']=='TAINTED')}
    return {'changes':changes,'gates':gates,'failed':failed,'totals':totals}
