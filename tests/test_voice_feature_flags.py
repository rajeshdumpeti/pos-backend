"""VOICE-M3: feature flags endpoint, settings and the entry-source header."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.application.feature_flags import VoiceEntryMode, voice_entry_enabled
from app.application.planning import normalize_entry_source
from app.config import ConfigurationError, load_settings
from app.domain.authentication import AccountStatus
from app.infrastructure.planning import SafePlanDiagnostic
from app.main import create_app
from tests.planning_support import fixture
from tests.test_onboarding_api import Sessions

AUTH = {"Authorization": "Bearer valid-token", "X-Request-ID": "voice-test"}
CREATE_HEADERS = {**AUTH, "Idempotency-Key": "voice-create"}
PAYLOAD = {
    "title": "Synthetic voice item",
    "scheduled_for_date": "2026-09-23",
    "timezone": "America/New_York",
    "is_priority": False,
}
LOGGER_NAME = "pos.planning.persistence"


def make_client(
    environ: dict[str, str] | None = None,
    *,
    complete: bool = True,
    diagnostic=None,
    allowlist_for_account: bool = False,
):
    account_id, account, states, onboarding, store, plans = fixture()
    if allowlist_for_account:
        environ = {
            **(environ or {}),
            "VOICE_ENTRY_MODE": "allowlist",
            "VOICE_ENTRY_ALLOWLIST": f"{uuid4()}, {account_id} ,{uuid4()}",
        }
    if not complete:
        states.clear()
    if diagnostic is not None:
        plans.diagnostic = diagnostic
    settings = load_settings({"APP_ENV": "test", **(environ or {})})
    client = TestClient(
        create_app(
            settings,
            email_session_service=Sessions(account_id),
            onboarding_service=onboarding,
            plan_service=plans,
        ),
        raise_server_exceptions=False,
    )
    return client, account_id, account


# --------------------------------------------------------------------------- settings


def test_voice_entry_defaults_to_off_with_an_empty_allowlist():
    settings = load_settings({"APP_ENV": "test"})
    assert settings.voice_entry_mode is VoiceEntryMode.OFF
    assert settings.voice_entry_allowlist == frozenset()


@pytest.mark.parametrize("raw", ["off", "allowlist", "on", "ON", " Allowlist "])
def test_each_voice_entry_mode_is_accepted(raw):
    settings = load_settings({"APP_ENV": "test", "VOICE_ENTRY_MODE": raw})
    assert settings.voice_entry_mode is VoiceEntryMode(raw.strip().lower())


@pytest.mark.parametrize("raw", ["enabled", "true", "1", "offf", "everyone"])
def test_invalid_voice_entry_mode_fails_startup_without_echoing_the_value(raw):
    with pytest.raises(ConfigurationError) as caught:
        load_settings({"APP_ENV": "test", "VOICE_ENTRY_MODE": raw})
    assert raw not in str(caught.value)
    assert "VOICE_ENTRY_MODE" in str(caught.value)


def test_allowlist_is_parsed_with_whitespace_and_duplicates_tolerated():
    first, second = uuid4(), uuid4()
    raw = f"  {first} , {str(second).upper()},{first},  ,"
    settings = load_settings({"APP_ENV": "test", "VOICE_ENTRY_ALLOWLIST": raw})
    assert settings.voice_entry_allowlist == frozenset({first, second})


@pytest.mark.parametrize(
    "raw",
    [
        "not-a-uuid",
        f"{uuid4()},oops",
        str(uuid4()).replace("-", ""),
        f"{uuid4()} {uuid4()}",
        "{" + str(uuid4()) + "}",
        "12345678-1234-1234-1234-12345678901g",
    ],
)
def test_any_invalid_allowlist_uuid_fails_startup_without_echoing_it(raw):
    with pytest.raises(ConfigurationError) as caught:
        load_settings({"APP_ENV": "test", "VOICE_ENTRY_ALLOWLIST": raw})
    assert "VOICE_ENTRY_ALLOWLIST" in str(caught.value)
    assert raw not in str(caught.value)


def test_allowlist_is_validated_even_when_the_mode_is_off():
    with pytest.raises(ConfigurationError):
        load_settings(
            {"APP_ENV": "test", "VOICE_ENTRY_MODE": "off", "VOICE_ENTRY_ALLOWLIST": "bad"}
        )


# --------------------------------------------------------------------------- decision


def test_voice_entry_decision_per_mode():
    listed, other = uuid4(), uuid4()
    allowlist = frozenset({listed})
    assert voice_entry_enabled(VoiceEntryMode.OFF, allowlist, listed) is False
    assert voice_entry_enabled(VoiceEntryMode.ON, frozenset(), other) is True
    assert voice_entry_enabled(VoiceEntryMode.ALLOWLIST, allowlist, listed) is True
    assert voice_entry_enabled(VoiceEntryMode.ALLOWLIST, allowlist, other) is False
    assert voice_entry_enabled(VoiceEntryMode.ALLOWLIST, frozenset(), other) is False


# --------------------------------------------------------------------------- endpoint


def test_features_requires_authentication():
    client, *_ = make_client()
    missing = client.get("/api/v1/features")
    assert missing.status_code == 401
    assert missing.json()["code"] == "authentication_required"
    assert missing.headers["cache-control"] == "no-store"
    invalid = client.get("/api/v1/features", headers={"Authorization": "Bearer wrong"})
    assert invalid.status_code == 401
    assert set(invalid.json()) == {"code", "message", "request_id", "details"}


def test_features_follows_the_same_gate_as_today_and_plan():
    client, _, account = make_client({"VOICE_ENTRY_MODE": "on"})
    assert client.get("/api/v1/features", headers=AUTH).status_code == 200

    verified_at = account.email_verified_at
    account.email_verified_at = None
    unverified = client.get("/api/v1/features", headers=AUTH)
    assert unverified.status_code == 403
    assert unverified.json()["code"] == "email_verification_required"
    account.email_verified_at = verified_at

    account.status = AccountStatus.DISABLED
    disabled = client.get("/api/v1/features", headers=AUTH)
    assert disabled.json()["code"] == "account_unavailable"
    assert disabled.headers["cache-control"] == "no-store"
    account.status = AccountStatus.ACTIVE

    incomplete, *_ = make_client({"VOICE_ENTRY_MODE": "on"}, complete=False)
    response = incomplete.get("/api/v1/features", headers=AUTH)
    assert response.status_code == 403
    assert response.json()["code"] == "onboarding_required"
    assert response.headers["cache-control"] == "no-store"


def test_features_reports_dependency_outage_like_the_other_product_routes():
    client = TestClient(create_app(load_settings({"APP_ENV": "test"})), raise_server_exceptions=False)
    assert client.get("/api/v1/features").status_code == 401
    unavailable = client.get("/api/v1/features", headers=AUTH)
    assert unavailable.status_code == 503
    assert unavailable.json()["code"] == "dependency_unavailable"


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({}, False),
        ({"VOICE_ENTRY_MODE": "off"}, False),
        ({"VOICE_ENTRY_MODE": "on"}, True),
        ({"VOICE_ENTRY_MODE": "allowlist"}, False),
        ({"VOICE_ENTRY_MODE": "allowlist", "VOICE_ENTRY_ALLOWLIST": str(uuid4())}, False),
        ({"VOICE_ENTRY_MODE": "off", "VOICE_ENTRY_ALLOWLIST": "ALL"}, None),
    ],
)
def test_features_voice_entry_per_mode(environ, expected):
    if expected is None:  # invalid allowlist fails at startup, never at request time
        with pytest.raises(ConfigurationError):
            make_client(environ)
        return
    client, *_ = make_client(environ)
    response = client.get("/api/v1/features", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["voice_entry"] == {"enabled": expected}


def test_features_allowlist_in_and_out():
    listed_client, *_ = make_client(allowlist_for_account=True)
    other_client, *_ = make_client(
        {"VOICE_ENTRY_MODE": "allowlist", "VOICE_ENTRY_ALLOWLIST": str(uuid4())}
    )
    assert listed_client.get("/api/v1/features", headers=AUTH).json()["voice_entry"] == {
        "enabled": True
    }
    assert other_client.get("/api/v1/features", headers=AUTH).json()["voice_entry"] == {
        "enabled": False
    }


def test_features_response_has_exactly_the_closed_shape_and_no_store():
    client, account_id, _ = make_client(
        {"VOICE_ENTRY_MODE": "allowlist", "VOICE_ENTRY_ALLOWLIST": str(uuid4())}
    )
    response = client.get("/api/v1/features", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body == {"contract_version": "1.0", "voice_entry": {"enabled": False}}
    assert list(body) == ["contract_version", "voice_entry"]
    assert list(body["voice_entry"]) == ["enabled"]
    assert isinstance(body["voice_entry"]["enabled"], bool)
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["x-request-id"] == "voice-test"
    # Never reveals the mode, the allowlist or any account.
    text = response.text.lower()
    assert "allowlist" not in text and "mode" not in text and str(account_id) not in text


def test_features_does_not_leak_other_accounts_through_the_response():
    listed = uuid4()
    client, account_id, _ = make_client(
        {"VOICE_ENTRY_MODE": "allowlist", "VOICE_ENTRY_ALLOWLIST": str(listed)}
    )
    response = client.get("/api/v1/features", headers=AUTH)
    assert str(listed) not in response.text and str(account_id) not in response.text


# --------------------------------------------------------------------------- OpenAPI


def test_openapi_documents_features_and_the_entry_source_header():
    client, *_ = make_client()
    schema = client.get("/openapi.json").json()

    operation = schema["paths"]["/api/v1/features"]["get"]
    assert operation["operationId"] == "get_feature_flags"
    assert {"200", "401", "403", "500", "503"} <= set(operation["responses"])
    ref = operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("/FeatureFlagsResponse")
    components = schema["components"]["schemas"]
    assert components["FeatureFlagsResponse"]["additionalProperties"] is False
    assert components["VoiceEntryFlagResponse"]["additionalProperties"] is False
    assert set(components["FeatureFlagsResponse"]["properties"]) == {
        "contract_version",
        "voice_entry",
    }
    assert list(components["VoiceEntryFlagResponse"]["properties"]) == ["enabled"]
    assert operation["responses"]["200"]["headers"]["Cache-Control"]["schema"]["const"] == "no-store"

    create = schema["paths"]["/api/v1/plan/items"]["post"]
    header = next(p for p in create["parameters"] if p["name"] == "X-POS-Entry-Source")
    assert header["in"] == "header" and header.get("required", False) is False
    assert "manual" in header["description"] and "voice" in header["description"]
    # Declared as a plain optional string so no value can ever be rejected as a 422.
    assert "enum" not in str(header["schema"]) and "pattern" not in str(header["schema"])
    assert "422" in create["responses"]


def test_today_and_plan_contracts_do_not_mention_the_voice_flag():
    client, *_ = make_client()
    schema = client.get("/openapi.json").json()
    today = str(schema["components"]["schemas"]["TodayResponse"])
    assert "voice_entry" not in today
    assert "voice" not in str(schema["components"]["schemas"].get("ActionIdentifier", ""))


# --------------------------------------------------------------------------- header


def create_records(caplog):
    return [r for r in caplog.records if getattr(r, "operation", None) == "plan_item_create"]


@pytest.mark.parametrize("source", ["manual", "quick_log", "voice"])
def test_each_valid_entry_source_is_logged(source, caplog):
    caplog.set_level("INFO", logger=LOGGER_NAME)
    client, *_ = make_client(diagnostic=SafePlanDiagnostic())
    response = client.post(
        "/api/v1/plan/items", headers={**CREATE_HEADERS, "X-POS-Entry-Source": source}, json=PAYLOAD
    )
    assert response.status_code == 201
    (record,) = create_records(caplog)
    assert record.entry_source == source


@pytest.mark.parametrize(
    "value",
    ["Voice", "VOICE", "robot", "", " voice", "voice ", "x" * 33, "voice" + "x" * 40, "manual,voice"],
)
def test_unknown_empty_or_oversized_entry_source_is_unspecified_and_never_an_error(value, caplog):
    caplog.set_level("INFO", logger=LOGGER_NAME)
    client, *_ = make_client(diagnostic=SafePlanDiagnostic())
    response = client.post(
        "/api/v1/plan/items", headers={**CREATE_HEADERS, "X-POS-Entry-Source": value}, json=PAYLOAD
    )
    assert response.status_code == 201
    (record,) = create_records(caplog)
    assert record.entry_source == "unspecified"
    assert value.strip() == "" or value not in caplog.text


def test_missing_entry_source_is_unspecified(caplog):
    caplog.set_level("INFO", logger=LOGGER_NAME)
    client, *_ = make_client(diagnostic=SafePlanDiagnostic())
    assert client.post("/api/v1/plan/items", headers=CREATE_HEADERS, json=PAYLOAD).status_code == 201
    (record,) = create_records(caplog)
    assert record.entry_source == "unspecified"


def test_entry_source_is_not_part_of_idempotency(caplog):
    caplog.set_level("INFO", logger=LOGGER_NAME)
    client, *_ = make_client(diagnostic=SafePlanDiagnostic())
    first = client.post(
        "/api/v1/plan/items", headers={**CREATE_HEADERS, "X-POS-Entry-Source": "voice"}, json=PAYLOAD
    )
    assert first.status_code == 201
    for headers in (
        {**CREATE_HEADERS, "X-POS-Entry-Source": "manual"},
        {**CREATE_HEADERS, "X-POS-Entry-Source": "bogus"},
        {**CREATE_HEADERS, "X-POS-Entry-Source": ""},
        CREATE_HEADERS,
    ):
        replay = client.post("/api/v1/plan/items", headers=headers, json=PAYLOAD)
        assert replay.status_code == 201
        assert replay.json() == first.json()
    # A replay is not a new creation, so only the original request is logged.
    assert [r.entry_source for r in create_records(caplog)] == ["voice"]
    # A changed body is still a conflict, whatever the header says.
    changed = client.post(
        "/api/v1/plan/items",
        headers={**CREATE_HEADERS, "X-POS-Entry-Source": "voice"},
        json={**PAYLOAD, "title": "Different"},
    )
    assert changed.status_code == 409


def test_entry_source_does_not_change_the_stored_item_or_response():
    client, *_ = make_client()
    plain = client.post(
        "/api/v1/plan/items", headers={**AUTH, "Idempotency-Key": "plain"}, json=PAYLOAD
    ).json()
    voiced = client.post(
        "/api/v1/plan/items",
        headers={**AUTH, "Idempotency-Key": "voiced", "X-POS-Entry-Source": "voice"},
        json=PAYLOAD,
    ).json()
    strip = lambda item: {k: v for k, v in item.items() if k != "id"}  # noqa: E731
    assert strip(plain) == strip(voiced)
    assert "entry_source" not in str(plain) and "entry_source" not in str(voiced)
    fetched = client.get("/api/v1/plan/items/" + voiced["id"], headers=AUTH).json()
    assert fetched == voiced


def test_create_log_record_carries_no_item_content(caplog):
    caplog.set_level("INFO", logger=LOGGER_NAME)
    client, account_id, _ = make_client(diagnostic=SafePlanDiagnostic())
    client.post(
        "/api/v1/plan/items",
        headers={**CREATE_HEADERS, "X-POS-Entry-Source": "voice"},
        json=PAYLOAD,
    )
    (record,) = create_records(caplog)
    assert PAYLOAD["title"] not in caplog.text
    assert "Synthetic" not in caplog.text
    assert PAYLOAD["scheduled_for_date"] not in caplog.text
    assert str(account_id) not in caplog.text
    assert "@" not in caplog.text
    allowed = {"operation", "outcome", "request_id", "version", "entry_source"}
    custom = {
        key
        for key in record.__dict__
        if key
        not in logging_attributes()
    }
    assert custom == allowed


def logging_attributes():
    import logging

    return set(logging.LogRecord("n", 20, "p", 1, "m", None, None).__dict__) | {
        "message",
        "asctime",
        "taskName",
    }


def test_only_the_create_event_gets_an_entry_source_field(caplog):
    caplog.set_level("INFO", logger=LOGGER_NAME)
    diagnostic = SafePlanDiagnostic()
    diagnostic.emit("plan_item_update", "safe-id", uuid4(), uuid4(), 2, entry_source="voice")
    diagnostic.emit("plan_item_create", "safe-id", uuid4(), uuid4(), 1, entry_source="voice")
    diagnostic.emit("plan_item_create", "safe-id", uuid4(), uuid4(), 1, entry_source="Private title")
    update, voiced, unknown = caplog.records
    assert not hasattr(update, "entry_source")
    assert voiced.entry_source == "voice"
    assert unknown.entry_source == "unspecified"
    assert "Private" not in caplog.text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("manual", "manual"),
        ("quick_log", "quick_log"),
        ("voice", "voice"),
        (None, "unspecified"),
        ("", "unspecified"),
        ("Voice", "unspecified"),
        ("voice\n", "unspecified"),
        ("v" * 33, "unspecified"),
        ("unspecified", "unspecified"),
    ],
)
def test_normalize_entry_source(raw, expected):
    assert normalize_entry_source(raw) == expected
