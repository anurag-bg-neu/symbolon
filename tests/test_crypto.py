import hashlib
import secrets

import pytest

from core import crypto
from core.models import ProtocolError


def pt(name: str) -> bytes:
    return crypto.hash_to_group(name)


# --- canon -------------------------------------------------------------------

def test_canon_is_key_order_stable_and_compact():
    a = crypto.canon({"b": 1, "a": [1, 2], "c": {"y": 1, "x": 2}})
    b = crypto.canon({"c": {"x": 2, "y": 1}, "a": [1, 2], "b": 1})
    assert a == b == b'{"a":[1,2],"b":1,"c":{"x":2,"y":1}}'


def test_canon_bytes_become_lowercase_hex_and_utf8():
    assert crypto.canon({"k": b"\xab\xcd", "s": "é"}) == '{"k":"abcd","s":"é"}'.encode()


@pytest.mark.parametrize("bad", [{"f": 1.5}, {1: "x"}, {"s": {1, 2}}, object()])
def test_canon_rejects_non_canonical_types(bad):
    with pytest.raises(TypeError):
        crypto.canon(bad)


# --- commit_tokens -----------------------------------------------------------

def test_commit_tokens_order_independent():
    t = [pt("A"), pt("B"), pt("C")]
    assert crypto.commit_tokens(t) == crypto.commit_tokens(list(reversed(t)))


def test_commit_tokens_flag_off_matches_spec_formula():
    t = [pt("A"), pt("B"), pt("C")]
    ref = hashlib.sha256(b"genematch/v1/commit/" + b"".join(sorted(t))).digest()
    assert crypto.commit_tokens(t) == ref
    assert crypto.commit_tokens(t, None) == ref


def test_commit_tokens_salted_matches_formula_and_differs():
    t = [pt("A"), pt("B")]
    salt = secrets.token_bytes(16)
    ref = hashlib.sha256(b"genematch/v1/commit/" + salt + b"".join(sorted(t))).digest()
    assert crypto.commit_tokens(t, salt) == ref
    assert crypto.commit_tokens(t, salt) != crypto.commit_tokens(t)
    assert crypto.commit_tokens(t, salt) != crypto.commit_tokens(t, secrets.token_bytes(16))


def test_commit_tokens_salt_hex_round_trip():
    t = [pt("A")]
    salt = secrets.token_bytes(16)
    stored = salt.hex()
    assert crypto.commit_tokens(t, crypto.from_hex(stored, 16)) == crypto.commit_tokens(t, salt)


def test_commit_tokens_rejects_hex_salt():
    with pytest.raises(TypeError):
        crypto.commit_tokens([pt("A")], secrets.token_hex(16))


@pytest.mark.parametrize("n", [0, 15, 17, 32])
def test_commit_tokens_rejects_wrong_salt_length(n):
    with pytest.raises(ValueError):
        crypto.commit_tokens([pt("A")], bytes(n))


def test_commit_tokens_rejects_bad_token():
    with pytest.raises(ValueError):
        crypto.commit_tokens([b"short"])


# --- commit_blinded ----------------------------------------------------------

CTX = dict(session_id="s1", mode="detail", role="initiator")


def test_commit_blinded_matches_formula():
    pts = [pt("A"), pt("B")]
    body = ('{"blinded":["%s","%s"],"mode":"detail","role":"initiator","session_id":"s1"}'
            % (pts[0].hex(), pts[1].hex())).encode()
    assert crypto.commit_blinded(pts, **CTX) == hashlib.sha256(b"genematch/v1/blinded/" + body).digest()


def test_commit_blinded_deterministic():
    pts = [pt("A"), pt("B")]
    assert crypto.commit_blinded(pts, **CTX) == crypto.commit_blinded(list(pts), **CTX)


@pytest.mark.parametrize("change", [
    "point", "order", "session_id", "mode", "role",
])
def test_commit_blinded_detects_changes(change):
    pts = [pt("A"), pt("B")]
    base = crypto.commit_blinded(pts, **CTX)
    ctx = dict(CTX)
    other = list(pts)
    if change == "point":
        other[1] = pt("C")
    elif change == "order":
        other.reverse()
    elif change == "session_id":
        ctx["session_id"] = "s2"
    elif change == "mode":
        ctx["mode"] = "count"
    else:
        ctx["role"] = "joiner"
    assert crypto.commit_blinded(other, **ctx) != base


def test_commit_blinded_rejects_hex_input():
    with pytest.raises(ValueError):
        crypto.commit_blinded([pt("A").hex()], **CTX)


# --- signatures --------------------------------------------------------------

SEED = secrets.token_bytes(32)
PK = crypto.pubkey_from_seed(SEED)
OBJ = {"person_id": "alex", "count": 3}


def test_sign_verify_valid():
    sig = crypto.sign(SEED, crypto.CREDENTIAL, OBJ)
    assert crypto.verify(PK, crypto.CREDENTIAL, OBJ, sig)
    assert crypto.verify(PK.hex(), crypto.CREDENTIAL, OBJ, bytes.fromhex(sig))


def test_verify_tampered_object():
    sig = crypto.sign(SEED, crypto.CREDENTIAL, OBJ)
    assert not crypto.verify(PK, crypto.CREDENTIAL, {**OBJ, "count": 4}, sig)


def test_verify_wrong_prefix():
    sig = crypto.sign(SEED, crypto.CREDENTIAL, OBJ)
    assert not crypto.verify(PK, crypto.CLAIM, OBJ, sig)


def test_verify_wrong_key():
    sig = crypto.sign(SEED, crypto.CREDENTIAL, OBJ)
    other = crypto.pubkey_from_seed(secrets.token_bytes(32))
    assert not crypto.verify(other, crypto.CREDENTIAL, OBJ, sig)


@pytest.mark.parametrize("sig", ["ab" * 63, "", "zz" * 64, "AB" * 64, 123])
def test_verify_malformed_signature_never_raises(sig):
    assert crypto.verify(PK, crypto.CREDENTIAL, OBJ, sig) is False


def test_verify_malformed_pubkey_never_raises():
    sig = crypto.sign(SEED, crypto.CREDENTIAL, OBJ)
    assert crypto.verify("00", crypto.CREDENTIAL, OBJ, sig) is False


# --- group operations --------------------------------------------------------

def test_hash_to_group_deterministic_and_valid():
    assert pt("CFTR") == pt("CFTR")
    assert crypto.validate_point(pt("CFTR")) == pt("CFTR")


def test_hash_to_group_distinct_genes():
    assert pt("CFTR") != pt("HBB")


def test_hash_to_group_domain_separated():
    import rbcl
    other = rbcl.crypto_core_ristretto255_from_hash(
        hashlib.sha512(b"genematch/v1/other/" + b"CFTR").digest())
    raw = rbcl.crypto_core_ristretto255_from_hash(hashlib.sha512(b"CFTR").digest())
    assert pt("CFTR") not in (other, raw)


def test_hash_to_group_rejects_empty():
    with pytest.raises(ValueError):
        crypto.hash_to_group("")


def test_random_scalar_fresh_and_mutable():
    a, b = crypto.random_scalar(), crypto.random_scalar()
    assert isinstance(a, bytearray) and len(a) == 32 and a != b and any(a)


def test_mul_commutes():
    a, b, p = crypto.random_scalar(), crypto.random_scalar(), pt("CFTR")
    assert crypto.mul(a, crypto.mul(b, p)) == crypto.mul(b, crypto.mul(a, p))


def test_blind_set_matches_mul():
    a = crypto.random_scalar()
    pts = [pt("A"), pt("B")]
    assert crypto.blind_set(a, pts) == [crypto.mul(a, p) for p in pts]


@pytest.mark.parametrize("bad", [bytes(32), bytes(31), b"\xff" * 32, "ab" * 32, None])
def test_validate_point_rejects(bad):
    with pytest.raises(ProtocolError) as e:
        crypto.validate_point(bad)
    assert e.value.code == "INVALID_POINT"


@pytest.mark.parametrize("bad", [bytes(32), b"\xff" * 32, bytes(31)])
def test_mul_rejects_invalid_and_identity(bad):
    with pytest.raises(ProtocolError) as e:
        crypto.mul(crypto.random_scalar(), bad)
    assert e.value.code == "INVALID_POINT"


def test_mul_rejects_bad_scalar_length():
    with pytest.raises(ValueError):
        crypto.mul(b"\x01" * 31, pt("A"))


@pytest.mark.parametrize("bad", ["zz" * 32, "AB" * 32, "ab" * 31, "00" * 32, 7])
def test_point_from_hex_rejects(bad):
    with pytest.raises(ProtocolError) as e:
        crypto.point_from_hex(bad)
    assert e.value.code == "INVALID_POINT"


def test_point_from_hex_accepts_valid():
    assert crypto.point_from_hex(pt("A").hex()) == pt("A")


def test_from_hex_checks_length_and_case():
    assert crypto.from_hex("abcd", 2) == b"\xab\xcd"
    for bad, n in [("abc", None), ("ABCD", None), ("abcd", 3)]:
        with pytest.raises(ValueError):
            crypto.from_hex(bad, n)


def test_zero_buffer():
    b = crypto.random_scalar()
    crypto.zero_buffer(b)
    assert b == bytearray(32)
