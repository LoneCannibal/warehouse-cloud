import json
import logging

from warehouse_common.logs import JsonFormatter


def record(**extra) -> logging.LogRecord:
    entry = logging.LogRecord("app", logging.INFO, __file__, 1, "task %s", ("done",), None)
    entry.__dict__.update(extra)
    return entry


def test_output_is_one_json_object():
    line = JsonFormatter("picking").format(record())
    entry = json.loads(line)
    assert entry["service"] == "picking"
    assert entry["level"] == "INFO"
    assert entry["message"] == "task done"
    assert entry["ts"].endswith("Z")


def test_context_fields_are_top_level_so_log_queries_can_filter_on_them():
    entry = json.loads(JsonFormatter("picking").format(record(order_id="o1", task_id="t1", attempt=2)))
    assert entry["order_id"] == "o1"
    assert entry["task_id"] == "t1"
    assert entry["attempt"] == 2


def test_absent_context_is_left_out():
    assert "order_id" not in json.loads(JsonFormatter("picking").format(record()))
