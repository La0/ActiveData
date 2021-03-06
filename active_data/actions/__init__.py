# encoding: utf-8
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Author: Kyle Lahnakoski (kyle@lahnakoski.com)
#
from __future__ import absolute_import
from __future__ import division
from __future__ import unicode_literals

import flask
from flask import Response
from future.utils import text_type

from active_data import record_request
from active_data.actions import save_query
from jx_base import STRUCT
from jx_python import meta
from mo_dots import coalesce, join_field, split_field
from mo_json import value2json
from mo_logs import Log, strings
from mo_logs.strings import expand_template, unicode2utf8
from mo_threads import Till
from mo_times.dates import Date
from mo_times.durations import MINUTE

QUERY_TOO_LARGE = "Query is too large"


def send_error(active_data_timer, body, e):
    status = 400

    if QUERY_TOO_LARGE in e:
        status = 413

    record_request(flask.request, None, body, e)
    Log.warning("Could not process\n{{body}}", body=body.decode("latin1"), cause=e)
    e = e.__data__()
    e.meta.timing.total = active_data_timer.duration.seconds

    # REMOVE TRACES, BECAUSE NICER TO HUMANS
    # def remove_trace(e):
    #     e.trace = e.trace[0:1:]
    #     for c in listwrap(e.cause):
    #         remove_trace(c)
    # remove_trace(e)

    return Response(
        unicode2utf8(value2json(e)),
        status=status
    )


def replace_vars(text, params=None):
    """
    REPLACE {{vars}} WITH ENVIRONMENTAL VALUES
    """
    start = 0
    var = strings.between(text, "{{", "}}", start)
    while var:
        replace = "{{" + var + "}}"
        index = text.find(replace, 0)
        if index==-1:
            Log.error("could not find {{var}} (including quotes)", var=replace)
        end = index + len(replace)

        try:
            replacement = text_type(Date(var).unix)
            text = text[:index] + replacement + text[end:]
            start = index + len(replacement)
        except Exception as _:
            start += 1

        var = strings.between(text, "{{", "}}", start)

    text = expand_template(text, coalesce(params, {}))
    return text


def test_mode_wait(query):
    """
    WAIT FOR METADATA TO ARRIVE ON INDEX
    :param query: dict() OF REQUEST BODY
    :return: nothing
    """

    if not query["from"]:
        return

    try:
        metadata_manager = meta.singlton
        now = Date.now()
        end_time = now + MINUTE

        if query["from"].startswith("meta."):
            return

        alias = join_field(split_field(query["from"])[0:1])
        metadata_manager.meta.tables[alias].timestamp = now  # TRIGGER A METADATA RELOAD AFTER THIS TIME

        # MARK COLUMNS DIRTY
        metadata_manager.meta.columns.update({
            "clear": [
                "partitions",
                "count",
                "cardinality",
                "multi",
                "last_updated"
            ],
            "where": {"eq": {"es_index": alias}}
        })

        # BE SURE THEY ARE ON THE todo QUEUE FOR RE-EVALUATION
        cols = [c for c in metadata_manager.get_columns(table_name=query["from"], force=True) if c.jx_type not in STRUCT]
        for c in cols:
            Log.note("Mark {{column.names}} dirty at {{time}}", column=c, time=now)
            metadata_manager.todo.push(c)

        while end_time > now:
            # GET FRESH VERSIONS
            cols = [c for c in metadata_manager.get_columns(table_name=query["from"]) if c.jx_type not in STRUCT]
            for c in cols:
                if not c.last_updated or now >= c.last_updated or c.cardinality == None:
                    Log.note(
                        "wait for column (table={{col.es_index}}, name={{col.es_column}}) metadata to arrive",
                        col=c
                    )
                    break
            else:
                break
            Till(seconds=1).wait()
        for c in cols:
            Log.note(
                "fresh column name={{column.names}} updated={{column.last_updated|date}} parts={{column.partitions}}",
                column=c
            )
    except Exception as e:
        Log.warning("could not pickup columns", cause=e)


