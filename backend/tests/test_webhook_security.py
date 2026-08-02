import hashlib
import hmac

from app.api.routes_webhook import _valid_signature
from app.providers.forensics import Signal, aggregate, calibrate

SECRET = b"test-secret"


def sign(body: bytes, secret: bytes = SECRET) -> str:
    return "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()


def test_valid_signature_accepted(monkeypatch):
    monkeypatch.setattr("app.api.routes_webhook.settings.meta_app_secret", SECRET.decode())
    body = b'{"entry":[]}'
    assert _valid_signature(body, sign(body))


def test_tampered_body_rejected(monkeypatch):
    monkeypatch.setattr("app.api.routes_webhook.settings.meta_app_secret", SECRET.decode())
    assert not _valid_signature(b'{"entry":[{"evil":1}]}', sign(b'{"entry":[]}'))


def test_wrong_secret_rejected(monkeypatch):
    monkeypatch.setattr("app.api.routes_webhook.settings.meta_app_secret", SECRET.decode())
    body = b'{"entry":[]}'
    assert not _valid_signature(body, sign(body, b"attacker-secret"))


def test_missing_or_malformed_header_rejected(monkeypatch):
    monkeypatch.setattr("app.api.routes_webhook.settings.meta_app_secret", SECRET.decode())
    assert not _valid_signature(b"{}", None)
    assert not _valid_signature(b"{}", "deadbeef")
    assert not _valid_signature(b"{}", "sha1=deadbeef")


def test_calibration_is_monotonic_and_bounded():
    prev = -1.0
    for raw in [0.0, 0.25, 0.5, 0.75, 1.0]:
        p = calibrate("ai_generated_frames", raw)
        assert 0.0 <= p <= 1.0
        assert p >= prev
        prev = p


def test_calibration_never_reaches_certainty():
    """A detector at 1.0 must not claim certainty — they do not generalise that well."""
    assert calibrate("ai_generated_frames", 1.0) <= 0.85
    assert calibrate("face_manipulation", 1.0) <= 0.85


def test_unknown_signal_passes_through_uncalibrated():
    assert calibrate("brand_new_detector", 0.4) == 0.4
    assert calibrate("anything", None) is None


def test_aggregate_with_no_signals_yields_zero_confidence():
    prob, conf = aggregate([])
    assert prob is None and conf == 0.0


def test_aggregate_confidence_grows_with_signal_count():
    def sig(name, p):
        return Signal(name, p, p, 0.5, {})

    _, few = aggregate([sig("a", 0.8)])
    _, many = aggregate([sig(n, 0.8) for n in "abcde"])
    assert many > few
    assert many <= 1.0


def test_c2pa_outweighs_a_weak_statistical_detector():
    strong = Signal("c2pa", 0.0, 0.0, 0.95, {})
    weak = Signal("splice_recompression", 1.0, 0.6, 0.30, {})
    prob, _ = aggregate([strong, weak])
    assert prob < 0.25  # cryptographic provenance dominates
