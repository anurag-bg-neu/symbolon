"""Task 7.6 (core part): selective-disclosure presentations."""

import json
import secrets

import pytest

from core import crypto
from core.claims import claim_hash, make_presentation, select_claim, verify_presentation
from core.models import CLAIM_FINDING, Presentation, ProtocolError, SignedClaim, TrialRequest
from tests.helpers import make_person


@pytest.fixture
def alex(lab):
    return make_person(lab, "alex", ["CFTR", "GBA", "PAH"], findings=["BRCA1"])


def request(gene="BRCA1", trial_id="parp-demo-01", nonce=None) -> TrialRequest:
    return TrialRequest(request_id="r1", trial_id=trial_id, nonce=nonce or secrets.token_hex(16),
                        gene=gene, finding=CLAIM_FINDING, expires_at="2026-10-24T09:05:00Z")


def present(person, req) -> Presentation:
    claim = select_claim(person.claims, req.gene, req.finding)
    return make_presentation(claim, person.holder_seed, req.nonce, req.trial_id)


def code_of(fn) -> str:
    with pytest.raises(ProtocolError) as e:
        fn()
    return e.value.code


def test_valid_presentation_accepted(lab, alex):
    req = request()
    verify_presentation(present(alex, req), lab.pubkey, req)
    verify_presentation(present(alex, req), lab.pubkey.hex(), req)


@pytest.mark.parametrize("field,value", [
    ("finding", "something else"), ("salt", "00" * 16), ("person_id", "sam"),
    ("gene", "BRCA2"), ("holder_pubkey", "11" * 32),
])
def test_tampered_claim_rejected(lab, alex, field, value):
    req = request()
    p = present(alex, req)
    p = p.model_copy(update={"claim": p.claim.model_copy(update={field: value})})
    assert code_of(lambda: verify_presentation(p, lab.pubkey, req)) == "BAD_LAB_SIG"


def test_claim_from_other_lab_rejected(lab, other_lab, alex):
    req = request()
    assert code_of(lambda: verify_presentation(present(alex, req), other_lab.pubkey, req)) == "BAD_LAB_SIG"


def test_copied_claim_signed_with_other_holder_key(lab, alex):
    req = request()
    claim = alex.claims[0]
    thief = secrets.token_bytes(32)
    from core.claims import _present_body
    sig = crypto.sign(thief, crypto.PRESENT, _present_body(claim, req.nonce, req.trial_id))
    p = Presentation(claim=claim, trial_id=req.trial_id, nonce=req.nonce, holder_sig=sig)
    assert code_of(lambda: verify_presentation(p, lab.pubkey, req)) == "BAD_HOLDER_SIG"


def test_make_presentation_refuses_wrong_seed(alex):
    req = request()
    assert code_of(lambda: make_presentation(alex.claims[0], secrets.token_bytes(32),
                                             req.nonce, req.trial_id)) == "BAD_HOLDER_SIG"


def test_presentation_fields_tampered_after_signing(lab, alex):
    req = request()
    p = present(alex, req)
    other = p.model_copy(update={"nonce": secrets.token_hex(16)})
    assert code_of(lambda: verify_presentation(other, lab.pubkey, req)) == "BAD_HOLDER_SIG"


def test_nonce_mismatch(lab, alex):
    old = request()
    p = present(alex, old)            # validly signed, but for an older request
    new = request(nonce=secrets.token_hex(16))
    assert code_of(lambda: verify_presentation(p, lab.pubkey, new)) == "REQUEST_MISMATCH"


def test_trial_mismatch(lab, alex):
    p = present(alex, request(trial_id="other-trial"))
    req = request(nonce=p.nonce)
    assert code_of(lambda: verify_presentation(p, lab.pubkey, req)) == "REQUEST_MISMATCH"


def test_wrong_gene(lab):
    morgan = make_person(lab, "morgan", ["HBB"], findings=["BRCA2"])
    brca1_req = request(gene="BRCA1")
    claim = morgan.claims[0]
    p = make_presentation(claim, morgan.holder_seed, brca1_req.nonce, brca1_req.trial_id)
    assert code_of(lambda: verify_presentation(p, lab.pubkey, brca1_req)) == "WRONG_FINDING"


def test_select_claim_no_match(lab, alex):
    assert code_of(lambda: select_claim(alex.claims, "BRCA2", CLAIM_FINDING)) == "NO_MATCHING_CLAIM"
    jordan = make_person(lab, "jordan", ["HBB"])
    assert code_of(lambda: select_claim(jordan.claims, "BRCA1", CLAIM_FINDING)) == "NO_MATCHING_CLAIM"


def test_presentation_discloses_exactly_one_claim(lab):
    both = make_person(lab, "pat", ["CFTR"], findings=["BRCA1", "BRCA2"])
    p = present(both, request())
    data = json.loads(p.model_dump_json())
    assert set(data) == {"claim", "trial_id", "nonce", "holder_sig"}
    assert set(data["claim"]) == set(SignedClaim.model_fields)
    text = json.dumps(data)
    assert "BRCA2" not in text
    assert not any(t in text for t in both.credential.tokens)
    assert both.credential.commitment not in text
    assert "CFTR" not in text


def test_claim_hash_includes_salt_and_sig(alex):
    c = alex.claims[0]
    h = claim_hash(c)
    assert len(h) == 64
    assert claim_hash(c.model_copy(update={"salt": "00" * 16})) != h
    assert claim_hash(c.model_copy(update={"lab_sig": "00" * 64})) != h
