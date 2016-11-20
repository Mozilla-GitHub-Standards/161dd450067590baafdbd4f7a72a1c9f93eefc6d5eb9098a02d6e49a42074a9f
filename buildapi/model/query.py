from sqlalchemy import *
import buildapi.model.meta as meta
from buildapi.model.util import get_time_interval
from buildapi.lib.helpers import get_branches
from pylons.decorators.cache import beaker_cache

import math, re, time

def GetBranchName(longname):
    # nightlies don't have a branch set (bug 570814)
    if not longname:
        return None

    allBranches = get_branches()
    shortname = longname.split('/')[-1]
    maybeBranch = ''
    for branch in allBranches:
        if shortname.startswith(branch):
            if len(branch) > len(maybeBranch):
                maybeBranch = branch

    if not maybeBranch:
        maybeBranch = 'Unknown'
    return maybeBranch

def GetBuilds(branch=None, type='pending', rev=None):
    b  = meta.scheduler_db_meta.tables['builds']
    br = meta.scheduler_db_meta.tables['buildrequests']
    bs = meta.scheduler_db_meta.tables['buildsets']
    ss = meta.scheduler_db_meta.tables['sourcestamps']
    if type == 'pending':
        q = select([br.c.id,
                    ss.c.branch,
                    ss.c.revision,
                    br.c.buildername,
                    br.c.submitted_at,
            ])
        q = q.where(and_(br.c.buildsetid==bs.c.id, bs.c.sourcestampid==ss.c.id))
        q = q.where(and_(br.c.claimed_at==0, br.c.complete==0))
    elif type == 'running':
        q = select([b.c.id,
                    br.c.id.label('brid'),
                    ss.c.branch,
                    ss.c.revision,
                    br.c.buildername,
                    br.c.submitted_at,
                    br.c.claimed_at.label('last_heartbeat'),
                    br.c.claimed_by_name,
                    b.c.start_time,
                    b.c.number,
            ])
        # joins
        q = q.where(and_(b.c.brid == br.c.id,
                         br.c.buildsetid==bs.c.id,
                         bs.c.sourcestampid==ss.c.id))
        # conditions to get running builds
        # the b.c.finish_time excludes previous builds when we retry (multiple
        # builds per buildrequest in that situation)
        q = q.where(and_(br.c.claimed_at > 0,
                         br.c.complete == 0,
                         b.c.finish_time == None))
    # use an outer join to catch pending builds
    # can probably trim the list of columns a bunch
    elif type == 'revision':
        q = join(br, bs, br.c.buildsetid==bs.c.id) \
                .join(ss, bs.c.sourcestampid==ss.c.id) \
                .outerjoin(b, br.c.id == b.c.brid) \
                .select(ss.c.revision.like(rev[0] + '%')) \
                .with_only_columns([
                    br.c.id,
                    br.c.buildsetid,
                    ss.c.branch,
                    ss.c.revision,
                    br.c.buildername,
                    br.c.submitted_at,
                    br.c.claimed_at,
                    br.c.claimed_by_name,
                    b.c.start_time,
                    b.c.finish_time,
                    b.c.number,
                    br.c.results])

    if branch is not None:
      q = q.where(ss.c.branch.like('%' + branch[0] + '%'))

    query_results = q.execute()

    builds = {}
    if type == "running":
        # Mapping of (claimed_by_name, buildername, start_time, number) to list of results
        real_builds = {}
        for r in query_results:
            build_key = (r.claimed_by_name, r.buildername, r.start_time, r.number)
            if build_key not in real_builds:
                real_builds[build_key] = [r]
            else:
                real_builds[build_key].append(r)

        for build_key, requests in real_builds.items():
            real_branch = GetBranchName(requests[0]['branch'])
            if not real_branch:
                real_branch = 'Unknown'
            if real_branch not in builds:
                builds[real_branch] = {}

            this_result = dict(
                # These things shouldn't change between requests
                buildername=requests[0].buildername,
                last_heartbeat=requests[0].last_heartbeat,
                claimed_by_name=requests[0].claimed_by_name,
                start_time=requests[0].start_time,
                number=requests[0].number,

                # These do change between requests
                id=None,
                revision=None,
                request_ids=[],
                submitted_at=None,
                )

            for r in requests:
                if not this_result['request_ids']:
                    this_result['request_ids'].append(r.brid)
                    this_result['id'] = r.id
                    this_result['submitted_at'] = r.submitted_at
                    this_result['revision'] = r.revision
                else:
                    # Use the latest information for the id and revision
                    if r.brid > max(this_result['request_ids']):
                        this_result['id'] = r.id
                        this_result['revision'] = r.revision

                    # Use earliest information for submitted_at
                    if r.brid < min(this_result['request_ids']):
                        this_result['submitted_at'] = r.submitted_at

                    this_result['request_ids'].append(r.brid)

            revision = this_result.get('revision')
            if not revision:
                revision = 'Unknown'
            revision = revision[:12]
            if revision not in builds[real_branch]:
                builds[real_branch][revision] = []
            builds[real_branch][revision].append(this_result)

    else:
        for r in query_results:
            real_branch = GetBranchName(r['branch'])
            if not real_branch:
                real_branch = 'Unknown'
            revision = r['revision']
            if not revision:
                revision = 'Unknown'
            if real_branch not in builds:
                builds[real_branch] = {}
            if revision not in builds[real_branch]:
                builds[real_branch][revision] = []

            this_result = {}
            for key,value in r.items():
                if key not in ('branch','revision'):
                    this_result[key] = value
            builds[real_branch][revision].append(this_result)

    return builds

def GetHistoricBuilds(slave, count=20, greedy=True):
    b  = meta.status_db_meta.tables['builds']
    bs = meta.status_db_meta.tables['builders']
    s  = meta.status_db_meta.tables['slaves']
    m  = meta.status_db_meta.tables['masters']
    p  = meta.status_db_meta.tables['properties']
    bp = meta.status_db_meta.tables['build_properties']
    if slave is not None:
        q = select([b.c.id,
                    bs.c.name.label('buildname'),
                    b.c.buildnumber,
                    b.c.starttime,
                    b.c.endtime,
                    b.c.result,
                    s.c.name.label('slavename'),
                    m.c.name.label('master'),
                ])
        q = q.where(and_(b.c.slave_id==s.c.id,
                        b.c.builder_id==bs.c.id,
                        b.c.master_id==m.c.id))
        q = q.where(b.c.result != None)
        if greedy:
            q = q.where(s.c.name.like(slave+'%'))
        else:
            q = q.where(s.c.name==slave)
        q = q.order_by(b.c.id.desc()).limit(count)
    else:
        subq = select([b.c.id,
                       bs.c.name.label('buildname'),
                       b.c.buildnumber,
                       b.c.starttime,
                       b.c.endtime,
                       b.c.result,
                       b.c.slave_id,
                       b.c.master_id])
        subq = subq.where(b.c.builder_id == bs.c.id)
        subq = subq.order_by(b.c.id.desc()).limit(count)
        subq = subq.alias('t')
        q = select([subq.c.id, subq.c.buildname, subq.c.buildnumber, subq.c.starttime, subq.c.endtime, subq.c.result, s.c.name.label('slavename'), m.c.name.label('master')])
        q = q.where(subq.c.slave_id == s.c.id)
        q = q.where(subq.c.master_id == m.c.id)
        q = q.order_by(subq.c.id.desc())

    query_results = q.execute()

    q2 = select([p.c.value])
    q2 = q2.where(and_(bp.c.property_id==p.c.id,
                       "properties.name='buildername'",
                       "build_properties.build_id = :x"))

    builds = []
    for r in query_results:
        this_result = {}
        for key,value in r.items():
            this_result[str(key)] = value
        this_result['buildername'] = ""
        if 'id' in this_result:
            q2_results = q2.execute(x=this_result['id'])
            one_row = q2_results.fetchone()
            if one_row and 'value' in one_row:
                # Properties come wrapped in double-quotes. Strip them.
                this_result['buildername'] = one_row['value'].strip('"')
        builds.append(this_result)

    return builds

def GetPushes(branch, fromtime, totime):
    ch    = meta.scheduler_db_meta.tables['changes']
    ss_ch = meta.scheduler_db_meta.tables['sourcestamp_changes']
    ss    = meta.scheduler_db_meta.tables['sourcestamps']

    # this is a little complicated in order to cope with mobile
    # adding a second sourcestamp for every revision in m-c (etc).
    # get distinct revision/branch pairs from sourcestamps and
    # retrieve author from changes
    q = select([ss.c.revision, ss.c.branch, ch.c.author],
               and_(ss_ch.c.changeid == ch.c.changeid,
                    ss.c.id == ss_ch.c.sourcestampid))
    q = q.distinct()

    q = q.where(not_(or_(ch.c.branch.like('%unittest'),
                         ch.c.branch.like('%talos'))))
    if branch is not None:
        q = q.where(ch.c.branch.like('%' + branch + '%'))

    if fromtime is not None:
        q = q.where(ch.c.when_timestamp >= fromtime)
    if totime is not None:
        q = q.where(ch.c.when_timestamp <= totime)

    query_results = q.execute()
    pushes = {'TOTAL': 0}
    for r in query_results:
        a = r['author']
        if a not in pushes:
            pushes[a] = 0
        pushes[a] += 1
        pushes['TOTAL'] += 1

    return pushes
