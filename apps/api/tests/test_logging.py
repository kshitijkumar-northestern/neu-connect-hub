import json
import logging

from app.core.logging import JsonFormatter, request_id_var


def format_record(**extra: object) -> dict[str, object]:
    record = logging.LogRecord("app.test", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    for key, value in extra.items():
        setattr(record, key, value)
    return json.loads(JsonFormatter().format(record))  # type: ignore[no-any-return]


def test_records_are_json_with_level_logger_and_message() -> None:
    line = format_record()

    assert line["level"] == "INFO"
    assert line["logger"] == "app.test"
    assert line["message"] == "hello world"


def test_the_current_request_id_is_included() -> None:
    token = request_id_var.set("req-abc12345")
    try:
        assert format_record()["request_id"] == "req-abc12345"
    finally:
        request_id_var.reset(token)


def test_sensitive_extra_fields_are_redacted() -> None:
    line = format_record(password="hunter2", session_token="tok", api_key="k", user_id="u1")

    assert line["password"] == "[redacted]"
    assert line["session_token"] == "[redacted]"
    assert line["api_key"] == "[redacted]"
    assert line["user_id"] == "u1"
