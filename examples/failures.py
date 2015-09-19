from pyLibrary import strings, convert
from pyLibrary.debugs import constants
from pyLibrary.debugs.logs import Log, Except
from pyLibrary.dot import wrap, Dict, coalesce, unwraplist
from pyLibrary.env import http
from pyLibrary.queries import qb
from pyLibrary.queries.index import Index
from pyLibrary.queries.qb_usingES import FromES
from pyLibrary.queries.unique_index import UniqueIndex
from pyLibrary.strings import edit_distance
from pyLibrary.times.dates import Date

# DATE RANGE
from pyLibrary.times.timer import Timer

FROM_DATE = Date("today-2day")
TO_DATE = Date("today-1day")

config = wrap({
    "ActiveData":{
        # "url": "http://activedata.allizom.org/query",
        "url": "http://localhost:5000/query"
    },
    "Bugzilla":{
        "url": "http://elasticsearch-zlb.bugs.scl3.mozilla.com:9200/public_bugs",
        "host": "http://elasticsearch-zlb.bugs.scl3.mozilla.com",
        "port": 9200,
        "index": "public_bugs",
        "type": "bug_version",
        "debug": False
    }
})

_set = Dict()
_set.pyLibrary.env.http.default_headers = {"From": "https://wiki.mozilla.org/Auto-tools/Projects/ActiveData"}
_set.pyLibrary.env.big_data.MAX_STRING_SIZE = 100 * 1000 * 1000
constants.set(_set)


#WAIT FOR SERVICE TO BE ACTIVE
while True:
    try:
        result = http.post_json(config.ActiveData.url, data={
            "from": "unittest",
            "limit": 1
        })
        break
    except Exception, e:
        if "No connection could be made because the target machine actively refused it" in e:
            Log.note("wait for AD to respond")
        else:
            Log.warning("problem", e)


# SIMPLE LIST OF ALL TEST FAILURES
with Timer("get failures"):
    result = http.post_json(config.ActiveData.url, data={
        "from": "unittest.result.subtests",
        "select": [
            "_id",
            {"name": "subtest_name", "value": "name"},
            "message",
            {"name": "suite", "value": "run.suite"},
            {"name": "chunk", "value": "run.chunk"},
            {"name": "test", "value": "result.test"},
            {"name": "build_date", "value": "build_date"},
            {"name": "branch", "value": "build.branch"},
            {"name": "revision", "value":"build.revision12"}
        ],
        "where": {"and": [
            {"gte": {"run.timestamp": FROM_DATE}},
            {"lt": {"run.timestamp": TO_DATE}},
            {"eq": {"result.ok": False}},
            {"eq": {"ok": False}},
            {"eq": {"build.branch": "mozilla-inbound"}}
        ]},
        "limit": 100,
        "format": "list"
    })

if result.type == "ERROR":
    Log.error("problem", cause=Except.wrap(result))


Log.note("got {{num}} errors", num=len(result.data))

#GROUP TESTS, AND COUNT
groups = Index(keys=["suite", "test", "subtest_name"], data=result.data)

# WE NEED THE BUGZILLA TO INDICATE THE PROBLEMS THAT HAVE BEEN SOLVED
with Timer("pull from bzETL"):
    with FromES(settings=config.Bugzilla) as es:
        #PULL ALL INTERMITTENTS, I CAN NOT FIGURE OUT HOW TO LIMIT TO JUST FOUND FAILURES
        bugs = es.query({
            "from": "public_bugs",
            "select": ["bug_id", "short_desc"],
            "where": {"and": [
                {"gt": {"expires_on": Date.now().milli}},
                {"eq": {"keyword": "intermittent-failure"}},
                {"not": {"eq": {"bug_status": ["resolved", "verified", "closed"]}}},
            ]},
            "limit": 100000
        })

# FIND BEST MATCH FOR EACH TEST FAILURE
for r in groups:
    test_name = r.result.test
    if not r.message:
        for b in bugs.data:
            if b.short_desc.find(test_name) >= 0:
                r.best += [b]
    else:
        for b in bugs.data:
            desc = b.short_desc.split("|")[-1].strip()
            if b.short_desc.find(test_name) >= 0:
                for m in r.message:
                    score = edit_distance(m, desc)
                    if r.score < score:
                        pass
                    else:
                        r.score = score
                        r.best = b

data = qb.sort([
    Dict(
        count=len(r.others),
        chunk=r.run.chunk,
        suite=r.run.suite,
        test=r.result.test,
        message=coalesce(r.first_message, "missing test end" if r.result.missing_test_end else None),
        bug_id=unwraplist(r.best.bug_id),
        bug_desc=unwraplist(r.best.short_desc),
        first_seen_branch=r.first_branch,
        first_seen_timestamp=r.first_seen
    )
    for r in groups
], {"value": "count", "sort": -1})
tab = convert.list2tab(data, columns=["count", "suite", "test", "chunk", "message", "bug_id", "bug_desc", "first_seen_branch", "first_seen_timestamp"])
Log.note("\n{{tab}}", tab=tab)
