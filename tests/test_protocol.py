"""Task 3.2: honest runs for all demo couples, plus the owner-side credential check."""

import json

import pytest

from core import crypto
from core.models import ProtocolError
from core.protocol import DONE, FAILED, Session
from tests.helpers import COUPLES, DEMO_GENES, make_pair, make_person, new_session_id, run_pair


@pytest.mark.parametrize("couple", COUPLES, ids=[c[0] for c in COUPLES])
@pytest.mark.parametrize("mode", ["detail", "count"])
@pytest.mark.parametrize("salt_a,salt_b", [(True, True), (False, False), (True, False)])
def test_couples(lab, couple, mode, salt_a, salt_b):
    name_a, genes_a, name_b, genes_b, expected = couple
    a = make_person(lab, name_a, genes_a, salt=salt_a)
    b = make_person(lab, name_b, genes_b, salt=salt_b)
    ra, rb, sa, sb = run_pair(lab, a, b, mode)

    assert ra == rb  # Requirement 3.4: same output for both
    assert ra.mode == mode
    assert ra.shared_count == len(expected)
    if mode == "detail":
        assert ra.shared_genes == expected
    else:
        assert ra.shared_genes is None
        dumped = ra.model_dump_json()
        assert not any(g in dumped for g in DEMO_GENES)
    for s in (sa, sb):
        assert s.state == DONE
        assert s.scalar_is_zeroed()


def test_empty_set(lab):
    a = make_person(lab, "a", [])
    b = make_person(lab, "b", ["CFTR"])
    ra, rb, _, _ = run_pair(lab, a, b, "detail")
    assert ra == rb and ra.shared_count == 0 and ra.shared_genes == []


def test_identical_sets(lab):
    a = make_person(lab, "a", ["CFTR", "HBB"])
    b = make_person(lab, "b", ["HBB", "CFTR"])
    ra, _, _, _ = run_pair(lab, a, b, "detail")
    assert ra.shared_genes == ["CFTR", "HBB"]


def test_blinded_sets_hide_tokens_and_vary_per_session(lab):
    a = make_person(lab, "alex", ["CFTR"])
    b = make_person(lab, "sam", ["CFTR"])
    sa, sb = make_pair(lab, a, b, "detail")
    sa.round0(); sb.round0()
    xa1 = sa._x[0]
    assert xa1.hex() not in a.credential.tokens
    # Two people with the same gene send different blinded points.
    assert sa._x[0] != sb._x[0]
    sa2, _ = make_pair(lab, a, b, "detail")
    sa2.round0()
    assert sa2._x[0] != xa1


def test_round1_payload_contains_only_design_fields(lab):
    a = make_person(lab, "alex", ["CFTR", "GBA"])
    b = make_person(lab, "sam", ["CFTR"])
    sa, sb = make_pair(lab, a, b, "detail")
    sa.round0(); sa.receive_commitment(sb.round0())
    msg = json.loads(sa.round1().model_dump_json())
    assert set(msg) == {"blinded", "person_id", "count", "commitment", "issued_at", "lab_sig"}
    text = json.dumps(msg)
    assert a.credential.commit_salt not in text
    assert not any(t in text for t in a.credential.tokens)
    assert "CFTR" not in text and "GBA" not in text


def test_transcript_has_only_prefixes_and_counts(lab):
    a = make_person(lab, "alex", ["CFTR", "GBA", "PAH"])
    b = make_person(lab, "sam", ["CFTR", "HEXA"])
    _, _, sa, _ = run_pair(lab, a, b, "detail")
    t = sa.transcript()
    assert t["sent"]["round1"]["count"] == 3
    assert t["received"]["round1"]["count"] == 2
    strings = []

    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, str):
            strings.append(o)

    walk({"sent": t["sent"], "received": t["received"]})
    assert strings and all(len(s) == 8 for s in strings)
    blob = json.dumps(t)
    assert not any(g in blob for g in DEMO_GENES)


@pytest.mark.parametrize("mode", ["detail", "count"])
def test_round2_order_preserved_in_detail_shuffled_in_count(lab, mode):
    genes = [f"SYN{i:02d}" for i in range(1, 21)]
    a = make_person(lab, "a", ["CFTR"])
    b = make_person(lab, "b", genes)
    sa, sb = make_pair(lab, a, b, mode)
    ca, cb = sa.round0(), sb.round0()
    sa.receive_commitment(cb); sb.receive_commitment(ca)
    sa.round1()
    xb = sb.round1()
    out = sa.round2(xb).points
    expected = [crypto.mul(sa._scalar, bytes.fromhex(x)).hex() for x in xb.blinded]
    assert sorted(out) == sorted(expected)
    if mode == "detail":
        assert out == expected
    else:
        assert out != expected  # 20! orderings; identity permutation is negligible


def test_zero_is_idempotent(lab):
    a = make_person(lab, "a", ["CFTR"])
    b = make_person(lab, "b", ["CFTR"])
    _, _, sa, _ = run_pair(lab, a, b, "count")
    sa.zero(); sa.zero()
    assert sa.scalar_is_zeroed()


# --- owner-side credential check ---------------------------------------------

def _session(lab, person, cred=None, genes=None):
    return Session("initiator", "detail", new_session_id(),
                   cred if cred is not None else person.credential,
                   genes if genes is not None else person.genes, lab.pubkey)


def _bad(lab, person, reason, cred=None, genes=None, pubkey=None):
    with pytest.raises(ProtocolError) as e:
        Session("initiator", "detail", new_session_id(),
                cred if cred is not None else person.credential,
                genes if genes is not None else person.genes,
                pubkey if pubkey is not None else lab.pubkey)
    assert e.value.code == "BAD_CREDENTIAL"
    assert e.value.reason == reason


@pytest.mark.parametrize("salt", [True, False])
def test_valid_credential_constructs(lab, salt):
    p = make_person(lab, "alex", ["CFTR", "GBA"], salt=salt)
    assert _session(lab, p).state == "NEW"


def test_constructor_accepts_dict_credential(lab):
    p = make_person(lab, "alex", ["CFTR"])
    assert _session(lab, p, cred=p.credential.model_dump()).state == "NEW"


def test_token_swapped(lab):
    p = make_person(lab, "alex", ["CFTR", "GBA"])
    other = crypto.mul(lab.k_lab, crypto.hash_to_group("HBB")).hex()
    cred = p.credential.model_copy(update={"tokens": [p.credential.tokens[0], other]})
    _bad(lab, p, "commitment", cred=cred)


def test_salt_removed(lab):
    p = make_person(lab, "alex", ["CFTR"])
    _bad(lab, p, "commitment", cred=p.credential.model_copy(update={"commit_salt": None}))


def test_salt_added(lab):
    p = make_person(lab, "alex", ["CFTR"], salt=False)
    cred = p.credential.model_copy(update={"commit_salt": "00" * 16})
    _bad(lab, p, "commitment", cred=cred)


def test_salt_changed(lab):
    p = make_person(lab, "alex", ["CFTR"])
    _bad(lab, p, "commitment", cred=p.credential.model_copy(update={"commit_salt": "11" * 16}))


def test_salt_wrong_length(lab):
    p = make_person(lab, "alex", ["CFTR"])
    # model_copy skips validation, so the constructor's own check must catch it.
    _bad(lab, p, "salt", cred=p.credential.model_copy(update={"commit_salt": "00" * 15}))
    _bad(lab, p, "salt", cred=p.credential.model_copy(update={"commit_salt": "xyz"}))


def test_tampered_count_fails_on_signature_first(lab):
    p = make_person(lab, "alex", ["CFTR", "GBA"])
    _bad(lab, p, "lab_sig", cred=p.credential.model_copy(update={"count": 1}))


def test_wrong_lab_key(lab, other_lab):
    p = make_person(lab, "alex", ["CFTR"])
    _bad(lab, p, "lab_sig", pubkey=other_lab.pubkey)


def test_genes_length_mismatch(lab):
    p = make_person(lab, "alex", ["CFTR", "GBA"])
    _bad(lab, p, "length", genes=["CFTR"])


def test_genes_unsorted(lab):
    p = make_person(lab, "alex", ["CFTR", "GBA"])
    _bad(lab, p, "genes", genes=["GBA", "CFTR"])


def test_invalid_token_point(lab):
    p = make_person(lab, "alex", ["CFTR"])
    _bad(lab, p, "token", cred=p.credential.model_copy(update={"tokens": ["00" * 32]}))


def test_malformed_credential(lab):
    p = make_person(lab, "alex", ["CFTR"])
    bad = p.credential.model_dump()
    bad["lab_sig"] = "XYZ"
    _bad(lab, p, "format", cred=bad)


def test_failed_constructor_state(lab):
    p = make_person(lab, "alex", ["CFTR"])
    s = object.__new__(Session)
    try:
        Session.__init__(s, "initiator", "detail", "sid",
                         p.credential.model_copy(update={"count": 2}), p.genes, lab.pubkey)
    except ProtocolError:
        pass
    assert s.state == FAILED
