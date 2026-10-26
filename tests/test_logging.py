import json
import logging

from logging_setup import JsonFormatter


def make_record(**extra):
    record = logging.LogRecord("deployment_api", logging.INFO, __file__, 1, "playbook_finished", None, None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


class TestJsonFormatter:
    def test_basic_fields(self):
        payload = json.loads(JsonFormatter().format(make_record()))
        assert payload["level"] == "INFO"
        assert payload["logger"] == "deployment_api"
        assert payload["event"] == "playbook_finished"
        assert "ts" in payload

    def test_extra_fields_on_top_level(self):
        payload = json.loads(JsonFormatter().format(make_record(run_id="r1", status=0)))
        assert payload["run_id"] == "r1"
        assert payload["status"] == 0
        assert "pathname" not in payload

    def test_one_line_even_with_newlines(self):
        line = JsonFormatter().format(make_record(output="a\nb"))
        assert "\n" not in line

    def test_exception_included(self):
        try:
            raise ValueError("boom")
        except ValueError:
            import sys
            record = logging.LogRecord("x", logging.ERROR, __file__, 1, "failed", None, sys.exc_info())
        assert "ValueError: boom" in json.loads(JsonFormatter().format(record))["exc"]
