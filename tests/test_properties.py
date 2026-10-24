"""Requirement-linked property-based tests for the GeneMatch crypto core."""

from __future__ import annotations

from unittest.mock import patch

import pytest
import rbcl
from hypothesis import given, settings, strategies as st

from core import crypto
from core.models import MAX_SET, ProtocolError
from core.protocol import Session
from tests.helpers import BIG_PANEL, Lab, make_person, run_pair

# ristretto255's prime-order scalar field. Canonical little-endian encodings make
# generated failures reproducible and shrinkable without weakening production's
# requirement to obtain session scalars from libsodium's CSPRNG.
SCALAR_ORDER = 2**252 + 27742317777372353535851937790883648493
SCALAR_INTEGERS = st.integers(min_value=1, max_value=SCALAR_ORDER - 1)
SCALARS = SCALAR_INTEGERS.map(lambda value: value.to_bytes(32, "little"))

AR_GENES = tuple(
    entry["gene"] for entry in BIG_PANEL if entry["inheritance"] == "AR"
)
GENES = st.sampled_from(AR_GENES)
GENE_SETS = st.sets(GENES, min_size=0, max_size=MAX_SET).map(sorted)
NONEMPTY_GENE_SETS = st.sets(GENES, min_size=1, max_size=MAX_SET).map(sorted)
SHUFFLEABLE_GENE_SETS = st.sets(
    GENES, min_size=2, max_size=MAX_SET
).map(sorted)
MODES = st.sampled_from(["count", "detail"])
INVALID_POINTS = st.binary(min_size=32, max_size=32).filter(
    lambda value: value != crypto.IDENTITY
    and not rbcl.crypto_core_ristretto255_is_valid_point(value)
)


@st.composite
def distinct_scalars(draw: st.DrawFn) -> tuple[bytes, bytes]:
    """Generate two distinct canonical non-zero scalars without rejection."""
    first = draw(SCALAR_INTEGERS)
    offset = draw(st.integers(min_value=1, max_value=SCALAR_ORDER - 2))
    second = ((first - 1 + offset) % (SCALAR_ORDER - 1)) + 1
    return first.to_bytes(32, "little"), second.to_bytes(32, "little")


@st.composite
def gene_sets_with_permutations(
    draw: st.DrawFn,
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Generate two logical sets and a non-identity permutation of each."""
    genes_a = draw(SHUFFLEABLE_GENE_SETS)
    genes_b = draw(SHUFFLEABLE_GENE_SETS)
    shuffled_a = list(
        draw(st.permutations(genes_a).filter(lambda order: list(order) != genes_a))
    )
    shuffled_b = list(
        draw(st.permutations(genes_b).filter(lambda order: list(order) != genes_b))
    )
    return genes_a, shuffled_a, genes_b, shuffled_b


@settings(max_examples=25, deadline=None)
@given(genes_a=GENE_SETS, genes_b=GENE_SETS, mode=MODES)
def test_p1_correctness(genes_a: list[str], genes_b: list[str], mode: str) -> None:
    """P1 / Requirements 3.4, 5.5, 5.6: PSI equals the true intersection."""
    lab = Lab()
    alice = make_person(lab, "alice", genes_a)
    bob = make_person(lab, "bob", genes_b)

    result_a, result_b, _, _ = run_pair(lab, alice, bob, mode)
    expected = sorted(set(genes_a) & set(genes_b))

    assert result_a == result_b
    assert result_a.shared_count == len(expected)
    if mode == "detail":
        assert result_a.shared_genes == expected
    else:
        assert result_a.shared_genes is None


@settings(max_examples=100, deadline=None)
@given(a=SCALARS, b=SCALARS, gene=GENES)
def test_p2_commutativity(a: bytes, b: bytes, gene: str) -> None:
    """P2 / Requirements 5.5, 5.6: double blinding commutes."""
    point = crypto.hash_to_group(gene)
    assert crypto.mul(a, crypto.mul(b, point)) == crypto.mul(
        b, crypto.mul(a, point)
    )


@settings(max_examples=15, deadline=None)
@given(case=gene_sets_with_permutations(), mode=MODES)
def test_p3_order_independence(
    case: tuple[list[str], list[str], list[str], list[str]], mode: str
) -> None:
    """P3 / Requirements 5.5, 5.6: input order cannot change the result."""
    genes_a, shuffled_a, genes_b, shuffled_b = case
    lab = Lab()

    baseline_a = make_person(lab, "alice", genes_a, salt=False)
    baseline_b = make_person(lab, "bob", genes_b, salt=False)
    baseline, baseline_peer, _, _ = run_pair(
        lab, baseline_a, baseline_b, mode
    )

    reordered_a = make_person(lab, "alice", shuffled_a, salt=False)
    a_only, a_only_peer, _, _ = run_pair(lab, reordered_a, baseline_b, mode)

    reordered_b = make_person(lab, "bob", shuffled_b, salt=False)
    b_only, b_only_peer, _, _ = run_pair(lab, baseline_a, reordered_b, mode)

    assert baseline == baseline_peer == a_only == a_only_peer
    assert baseline == b_only == b_only_peer


@settings(max_examples=100, deadline=None)
@given(candidate=INVALID_POINTS)
def test_p4_validation(candidate: bytes) -> None:
    """P4 / Requirement 5.4: invalid encodings and identity are rejected."""
    for invalid in (candidate, crypto.IDENTITY):
        with pytest.raises(ProtocolError) as error:
            crypto.validate_point(invalid)
        assert error.value.code == "INVALID_POINT"


@settings(max_examples=50, deadline=None)
@given(keys=distinct_scalars(), gene=GENES)
def test_p5_key_separation(keys: tuple[bytes, bytes], gene: str) -> None:
    """P5 / Requirements 1.1, 1.4: distinct lab keys issue distinct tokens."""
    key_a, key_b = keys
    lab_a = Lab(k_lab=bytearray(key_a))
    lab_b = Lab(k_lab=bytearray(key_b))

    token_a = make_person(lab_a, "holder", [gene], salt=False).credential.tokens[0]
    token_b = make_person(lab_b, "holder", [gene], salt=False).credential.tokens[0]

    assert token_a != token_b


@settings(max_examples=25, deadline=None)
@given(genes=NONEMPTY_GENE_SETS, mode=MODES, scalars=distinct_scalars())
def test_p6_unlinkability(
    genes: list[str], mode: str, scalars: tuple[bytes, bytes]
) -> None:
    """P6 / Requirements 5.1, 5.2: fresh scalars rerandomize wire points."""
    lab = Lab()
    alice = make_person(lab, "alice", genes)
    first = Session(
        "initiator", mode, "property-run-one", alice.credential, alice.genes, lab.pubkey
    )
    second = Session(
        "initiator", mode, "property-run-two", alice.credential, alice.genes, lab.pubkey
    )

    with patch(
        "core.protocol.crypto.random_scalar",
        side_effect=[bytearray(scalars[0]), bytearray(scalars[1])],
    ):
        first.round0()
        second.round0()

    # A peer commitment is needed to expose each session's own round-1 wire
    # message; it is not verified until round 2 and is irrelevant to this property.
    placeholder_peer_commitment = {"h": bytes(32).hex()}
    first.receive_commitment(placeholder_peer_commitment)
    second.receive_commitment(placeholder_peer_commitment)
    first_wire = first.round1().blinded
    second_wire = second.round1().blinded

    # Compare sets so the independent protocol shuffle cannot satisfy the property
    # merely by changing order: the blinded point values themselves must differ.
    assert set(first_wire) != set(second_wire)
