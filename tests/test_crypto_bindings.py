"""Task 1.2: the ristretto255 bindings we depend on exist and behave as expected."""

import hashlib

import pytest
import rbcl

REQUIRED = [
    "crypto_core_ristretto255_from_hash",
    "crypto_scalarmult_ristretto255",
    "crypto_core_ristretto255_scalar_random",
    "crypto_core_ristretto255_is_valid_point",
]


@pytest.mark.parametrize("name", REQUIRED)
def test_binding_exists(name):
    assert callable(getattr(rbcl, name))


def test_from_hash_returns_valid_point():
    p = rbcl.crypto_core_ristretto255_from_hash(hashlib.sha512(b"x").digest())
    assert len(p) == 32
    assert rbcl.crypto_core_ristretto255_is_valid_point(p)


def test_is_valid_point_rejects_garbage():
    assert not rbcl.crypto_core_ristretto255_is_valid_point(b"\xff" * 32)


def test_libsodium_accepts_identity_so_we_must_reject_it():
    # Documents why core.crypto.validate_point checks bytes(32) explicitly.
    assert rbcl.crypto_core_ristretto255_is_valid_point(bytes(32))
