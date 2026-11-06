"""Project-wide pytest safety hooks."""

from typing import Any

import pytest

from app.infrastructure.database_diagnostics import redact_database_diagnostic


def pytest_exception_interact(
    node: Any,
    call: Any,
    report: Any,
) -> None:
    """Redact database credentials before pytest renders a failing traceback."""
    del node, call
    if report.longrepr is not None:
        report.longrepr = redact_database_diagnostic(report.longreprtext)


# Generated locally for tests only; independent of the host's CA installation.
@pytest.fixture
def trusted_ca_file(tmp_path):
    from datetime import datetime, timedelta, UTC

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "POS test CA")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    path = tmp_path / "test-ca.pem"
    path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    return path
