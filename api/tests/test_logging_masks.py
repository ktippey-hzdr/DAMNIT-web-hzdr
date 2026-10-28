"""Passwords inside connection URLs never reach the log."""

import structlog
from pydantic import SecretStr
from starlette.datastructures import Address

from damnit_api import _logging
from damnit_api._logging import mask_url_credentials


def test_password_in_a_url_is_masked_at_any_depth():
    event = {
        "event": "Settings",
        "hzdr_health": {
            "mongo_uri": "mongodb://FWKTro:s3cr%2Ft@vlsmongo.fz-rossendorf.de/db?authSource=db",
            "kafka_bootstrap": "149.220.77.19:9092",
        },
        "urls": ["https://user:pw@example.org/x", ("amqp://a:b@host:5672",)],
    }
    masked = mask_url_credentials(None, "debug", event)
    assert masked["hzdr_health"]["mongo_uri"] == (
        "mongodb://FWKTro:***@vlsmongo.fz-rossendorf.de/db?authSource=db"
    )
    assert masked["hzdr_health"]["kafka_bootstrap"] == "149.220.77.19:9092"
    assert masked["urls"] == [
        "https://user:***@example.org/x",
        ("amqp://a:***@host:5672",),
    ]
    assert "s3cr" not in repr(masked)


def test_urls_without_a_password_are_left_alone():
    event = {
        "a": "mongodb://localhost:27017",
        "b": "ldaps://ldap.fz-rossendorf.de:636",
        "c": "https://vlsscicat.fz-rossendorf.de/datasets/HZDR%2F5993c5ae",
        "d": "mailto:someone@hzdr.de",
        "e": SecretStr("x"),
        "f": 3,
        "client": Address("127.0.0.1", 50292),
    }
    assert mask_url_credentials(None, "info", event) == event


def test_the_configured_pipeline_masks():
    _logging.configure(level="DEBUG", debug=False)
    assert mask_url_credentials in structlog.get_config()["processors"]
