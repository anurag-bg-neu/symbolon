"""core/labfile.py: positive and negative tests for every function."""

import secrets

import pytest

from core import crypto
from core.labfile import (
    FORMAT,
    LabFileError,
    make_lab_file,
    verify_connect_claims,
    verify_lab_file,
)
from tests.helpers import Lab, make_person


@pytest.fixture
def alex(lab):
    return make_person(lab, "alex", ["CFTR", "GBA", "PAH"], findings=["BRCA1"])


def doc_for(lab, person, name="Alex"):
    return make_lab_file(lab.seed, person.credential, person.genes, name)


def code(fn):
    with pytest.raises(LabFileError) as e:
        fn()
    return e.value.code


def test_valid_file_verifies(lab, alex):
    d = doc_for(lab, alex)
    assert d["format"] == FORMAT and d["synthetic"] is True
    cred, genes, name = verify_lab_file(d, lab.pubkey, "alex")
    assert genes == ["CFTR", "GBA", "PAH"] and name == "Alex" and cred == alex.credential
    verify_lab_file(d, lab.pubkey.hex(), "alex")


def test_make_rejects_unsorted_or_wrong_count(lab, alex):
    with pytest.raises(ValueError):
        make_lab_file(lab.seed, alex.credential, ["PAH", "CFTR", "GBA"], "Alex")
    with pytest.raises(ValueError):
        make_lab_file(lab.seed, alex.credential, ["CFTR"], "Alex")


@pytest.mark.parametrize("doc", [None, [], "x", {"format": "other"}])
def test_not_a_lab_file(lab, doc):
    assert code(lambda: verify_lab_file(doc, lab.pubkey, "alex")) == "NOT_LAB_FILE"


@pytest.mark.parametrize("drop", ["credential", "genes", "display_name", "genes_sig"])
def test_malformed(lab, alex, drop):
    d = doc_for(lab, alex)
    del d[drop]
    assert code(lambda: verify_lab_file(d, lab.pubkey, "alex")) == "MALFORMED_FILE"


def test_genes_must_be_strings(lab, alex):
    d = doc_for(lab, alex)
    d["genes"] = [1, 2, 3]
    assert code(lambda: verify_lab_file(d, lab.pubkey, "alex")) == "MALFORMED_FILE"


def test_other_lab_not_signed(alex, other_lab, lab):
    d = doc_for(lab, alex)
    assert code(lambda: verify_lab_file(d, other_lab.pubkey, "alex")) == "NOT_SIGNED"


def test_tampered_credential_field(lab, alex):
    d = doc_for(lab, alex)
    d["credential"]["count"] = 2
    assert code(lambda: verify_lab_file(d, lab.pubkey, "alex")) == "NOT_SIGNED"


@pytest.mark.parametrize("field,value", [
    ("genes", ["CFTR", "GBA", "HEXA"]),      # relabelled gene, same count, still sorted
    ("display_name", "Mallory"),
    ("person_id", "sam"),
    ("genes_sig", "00" * 64),
])
def test_tampered_lab_file_fields(lab, alex, field, value):
    d = doc_for(lab, alex)
    d[field] = value
    assert code(lambda: verify_lab_file(d, lab.pubkey, "alex")) == "TAMPERED"


def test_tampered_token_caught_by_owner_check(lab, alex):
    """Swapping a token keeps the signatures valid; the commitment check catches it."""
    d = doc_for(lab, alex)
    d["credential"]["tokens"][0] = crypto.mul(lab.k_lab, crypto.hash_to_group("HBB")).hex()
    assert code(lambda: verify_lab_file(d, lab.pubkey, "alex")) == "TAMPERED"


def test_wrong_person(lab):
    sam = make_person(lab, "sam", ["CFTR", "HEXA"])
    d = make_lab_file(lab.seed, sam.credential, sam.genes, "Sam")
    with pytest.raises(LabFileError) as e:
        verify_lab_file(d, lab.pubkey, "alex")
    assert e.value.code == "WRONG_PERSON" and "This file belongs to Sam" in e.value.user_message


def test_error_messages_carry_no_data(lab, alex):
    d = doc_for(lab, alex)
    d["genes"] = ["CFTR", "GBA", "HEXA"]
    with pytest.raises(LabFileError) as e:
        verify_lab_file(d, lab.pubkey, "alex")
    assert "CFTR" not in e.value.user_message and e.value.to_dict()["code"] == "TAMPERED"


def test_connect_claims_valid(lab, alex):
    claims = [c.model_dump() for c in alex.claims]
    pub = crypto.pubkey_from_seed(alex.holder_seed).hex()
    out = verify_connect_claims(claims, lab.pubkey, "alex", pub)
    assert [c.gene for c in out] == ["BRCA1"]
    assert verify_connect_claims([], lab.pubkey, "alex", pub) == []


@pytest.mark.parametrize("case", ["other_holder", "other_person", "other_lab", "tampered",
                                  "not_list", "malformed"])
def test_connect_claims_rejected(lab, other_lab, alex, case):
    claims = [c.model_dump() for c in alex.claims]
    pub = crypto.pubkey_from_seed(alex.holder_seed).hex()
    person, key = "alex", lab.pubkey
    if case == "other_holder":
        pub = crypto.pubkey_from_seed(secrets.token_bytes(32)).hex()
    elif case == "other_person":
        person = "sam"
    elif case == "other_lab":
        key = other_lab.pubkey
    elif case == "tampered":
        claims[0]["finding"] = "x"
    elif case == "not_list":
        claims = {"a": 1}
    else:
        claims = [{"gene": "BRCA1"}]
    assert code(lambda: verify_connect_claims(claims, key, person, pub)) == "BAD_CLAIMS"
