import pytest
from pydantic import ValidationError

from core.models import (
    USER_MESSAGES,
    BlindedSet,
    Commitment,
    Credential,
    DoubleBlinded,
    ProtocolError,
    Result,
    SignedClaim,
)

H32 = "ab" * 32
H64 = "cd" * 64

DESIGN_CODES = {
    "BAD_SIGNATURE", "COMMITMENT_MISMATCH", "COUNT_MISMATCH", "SET_TOO_LARGE",
    "INVALID_POINT", "PEER_DECLINED", "SESSION_EXPIRED", "RELAY_UNAVAILABLE",
    "OUT_OF_ORDER", "BAD_CREDENTIAL", "BAD_LAB_SIG", "BAD_HOLDER_SIG",
    "NONCE_REUSED", "NONCE_EXPIRED", "WRONG_FINDING", "NO_MATCHING_CLAIM",
    "REQUEST_MISMATCH", "PEER_FAILED", "MALFORMED_MESSAGE",
}


def _cred(**kw):
    base = dict(person_id="p", tokens=[H32], count=1, commitment=H32,
                issued_at="2026-10-24T09:00:00Z", lab_sig=H64)
    base.update(kw)
    return Credential(**base)


def test_credential_round_trip():
    c = _cred(commit_salt="00" * 16)
    assert Credential.model_validate_json(c.model_dump_json()) == c


def test_credential_salt_defaults_to_none():
    assert _cred().commit_salt is None


@pytest.mark.parametrize("bad", ["AB" * 32, "ab" * 31 + "a", "zz" * 32, "ab" * 31])
def test_hex_fields_reject_bad_hex(bad):
    with pytest.raises(ValidationError):
        _cred(commitment=bad)


def test_salt_must_be_16_bytes():
    with pytest.raises(ValidationError):
        _cred(commit_salt="00" * 15)


def test_messages_round_trip():
    for m in [
        Commitment(h=H32),
        BlindedSet(blinded=[H32], person_id="p", count=1, commitment=H32,
                   issued_at="t", lab_sig=H64),
        DoubleBlinded(points=[H32]),
        Result(mode="count", shared_count=1, shared_genes=None),
        SignedClaim(person_id="p", gene="BRCA1", finding="f", salt="00" * 16,
                    holder_pubkey=H32, issued_at="t", lab_sig=H64),
    ]:
        assert type(m).model_validate_json(m.model_dump_json()) == m


def test_blinded_set_has_no_salt_field():
    with pytest.raises(ValidationError):
        BlindedSet(blinded=[], person_id="p", count=0, commitment=H32,
                   issued_at="t", lab_sig=H64, commit_salt="00" * 16)


def test_every_code_has_a_user_message():
    assert DESIGN_CODES <= set(USER_MESSAGES)
    for msg in USER_MESSAGES.values():
        assert msg and "{" not in msg


def test_protocol_error_unknown_code_rejected():
    with pytest.raises(ValueError):
        ProtocolError("NOT_A_CODE")


def test_protocol_error_to_dict_hides_reason():
    e = ProtocolError("BAD_CREDENTIAL", "lab_sig")
    assert e.to_dict() == {"code": "BAD_CREDENTIAL",
                           "user_message": USER_MESSAGES["BAD_CREDENTIAL"]}
