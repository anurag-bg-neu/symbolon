import secrets
from datetime import datetime, timezone

import pytest

from core import crypto
from core.issuance import (
    IssuanceError,
    claim_signed_fields,
    credential_signed_fields,
    issue,
)
from core.models import CLAIM_FINDING, MAX_SET
from tests.helpers import BIG_PANEL, PANEL, Lab

HOLDER = crypto.pubkey_from_seed(secrets.token_bytes(32))
BRCA1 = {"gene": "BRCA1", "finding": CLAIM_FINDING}


def _issue(lab, genes, findings=(), **kw):
    return issue(lab.k_lab, lab.seed, "alex", genes, list(findings), HOLDER,
                 kw.pop("panel", PANEL), **kw)


def test_valid_credential(lab):
    cred, claims = _issue(lab, ["PAH", "CFTR", "GBA"], [BRCA1])
    assert cred.count == 3 == len(cred.tokens)
    assert crypto.verify(lab.pubkey, crypto.CREDENTIAL, credential_signed_fields(cred), cred.lab_sig)
    assert len(claims) == 1


def test_tokens_align_with_sorted_genes(lab):
    cred, _ = _issue(lab, ["PAH", "CFTR", "GBA"])
    for g, t in zip(["CFTR", "GBA", "PAH"], cred.tokens):
        assert t == crypto.mul(lab.k_lab, crypto.hash_to_group(g)).hex()


def test_tokens_are_not_raw_hashes(lab):
    cred, _ = _issue(lab, ["CFTR"])
    assert cred.tokens[0] != crypto.hash_to_group("CFTR").hex()


def test_commitment_matches_tokens_and_salt(lab):
    cred, _ = _issue(lab, ["CFTR", "GBA"])
    salt = crypto.from_hex(cred.commit_salt, 16)
    tokens = [bytes.fromhex(t) for t in cred.tokens]
    assert cred.commitment == crypto.commit_tokens(tokens, salt).hex()


def test_salt_present_only_when_flag_on(lab):
    on, _ = _issue(lab, ["CFTR"])
    off, _ = _issue(lab, ["CFTR"], salt_commitment=False)
    assert on.commit_salt is not None and len(on.commit_salt) == 32
    assert off.commit_salt is None
    tokens = [bytes.fromhex(t) for t in off.tokens]
    assert off.commitment == crypto.commit_tokens(tokens).hex()


def test_same_genes_commitments_linkable_only_with_flag_off(lab):
    a, _ = _issue(lab, ["CFTR", "HBB"], salt_commitment=False)
    b, _ = _issue(lab, ["CFTR", "HBB"], salt_commitment=False)
    assert a.commitment == b.commitment
    c, _ = _issue(lab, ["CFTR", "HBB"])
    d, _ = _issue(lab, ["CFTR", "HBB"])
    assert c.commitment != d.commitment


def test_issued_at_format(lab):
    now = datetime(2026, 10, 24, 9, 0, 0, tzinfo=timezone.utc)
    cred, _ = _issue(lab, ["CFTR"], now=now)
    assert cred.issued_at == "2026-10-24T09:00:00Z"


def test_empty_gene_list_allowed(lab):
    cred, _ = _issue(lab, [])
    assert cred.count == 0 and cred.tokens == []


@pytest.mark.parametrize("genes", [
    ["CFTR", "NOTAGENE"],   # not in panel
    ["DMD"],                # X-linked
    ["CFTR", "CFTR"],       # duplicate
])
def test_rejects_bad_genes(lab, genes):
    with pytest.raises(IssuanceError):
        _issue(lab, genes)


def test_rejects_more_than_max_set(lab):
    genes = [f"SYN{i:02d}" for i in range(1, MAX_SET + 2)]
    with pytest.raises(IssuanceError):
        _issue(lab, genes, panel=BIG_PANEL)
    cred, _ = _issue(lab, genes[:MAX_SET], panel=BIG_PANEL)
    assert cred.count == MAX_SET


@pytest.mark.parametrize("finding", [
    {"gene": "TP53", "finding": CLAIM_FINDING},              # not a claim gene
    {"gene": "BRCA1", "finding": "somatic tumor mutation"},  # wrong finding
])
def test_rejects_bad_findings(lab, finding):
    with pytest.raises(IssuanceError):
        _issue(lab, ["CFTR"], [finding])


def test_rejects_duplicate_findings(lab):
    with pytest.raises(IssuanceError):
        _issue(lab, ["CFTR"], [BRCA1, BRCA1])


def test_claims_fields_and_signature(lab):
    _, claims = _issue(lab, ["CFTR"], [BRCA1, {"gene": "BRCA2", "finding": CLAIM_FINDING}])
    assert [c.gene for c in claims] == ["BRCA1", "BRCA2"]
    for c in claims:
        assert c.holder_pubkey == HOLDER.hex()
        assert len(c.salt) == 32
        assert crypto.verify(lab.pubkey, crypto.CLAIM, claim_signed_fields(c), c.lab_sig)
    assert claims[0].salt != claims[1].salt


def test_claim_signature_fails_under_wrong_key(lab, other_lab):
    _, claims = _issue(lab, ["CFTR"], [BRCA1])
    c = claims[0]
    assert not crypto.verify(other_lab.pubkey, crypto.CLAIM, claim_signed_fields(c), c.lab_sig)


def test_claim_and_credential_signatures_not_interchangeable(lab):
    cred, _ = _issue(lab, ["CFTR"])
    assert not crypto.verify(lab.pubkey, crypto.CLAIM, credential_signed_fields(cred), cred.lab_sig)
