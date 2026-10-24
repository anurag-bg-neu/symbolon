"""Task 3.3: malicious partner or relay. Every abort leaves the victim FAILED and zeroed."""

import pytest

from core import crypto
from core.issuance import credential_signed_fields
from core.models import BlindedSet, DoubleBlinded, ProtocolError
from core.protocol import FAILED, Session
from tests.helpers import Lab, make_pair, make_person, new_session_id


@pytest.fixture
def couple(lab):
    a = make_person(lab, "alex", ["CFTR", "GBA", "PAH"])
    b = make_person(lab, "sam", ["CFTR", "HEXA"])
    return a, b


def to_round1(lab, a, b, mode="detail", sid=None):
    """Both sides through round 1. Returns (sa, sb, xa, xb)."""
    sa, sb = make_pair(lab, a, b, mode, sid)
    ca, cb = sa.round0(), sb.round0()
    sa.receive_commitment(cb)
    sb.receive_commitment(ca)
    return sa, sb, sa.round1(), sb.round1()


def assert_abort(session, fn, code):
    with pytest.raises(ProtocolError) as e:
        fn()
    assert e.value.code == code
    assert session.state == FAILED
    assert session.scalar_is_zeroed()
    return e.value


def resign(seed, msg: BlindedSet, **update) -> BlindedSet:
    m = msg.model_copy(update=update)
    return m.model_copy(update={"lab_sig": crypto.sign(seed, crypto.CREDENTIAL,
                                                        credential_signed_fields(m))})


def random_point() -> bytes:
    return crypto.hash_to_group("random-" + crypto.random_scalar().hex())


# --- round-1 checks ----------------------------------------------------------

def _meta_of(person):
    c = person.credential
    return {k: getattr(c, k) for k in ("person_id", "count", "commitment", "issued_at", "lab_sig")}


def test_inflated_set_count_mismatch(lab, couple):
    """Sam commits honestly to count+1 points under his real signed count of 2."""
    a, b = couple
    pts = [random_point() for _ in range(b.credential.count + 1)]
    sa = _committed_peer(lab, a, pts)
    msg = BlindedSet(blinded=[p.hex() for p in pts], **_meta_of(b))
    assert_abort(sa, lambda: sa.round2(msg), "COUNT_MISMATCH")


def test_deflated_set_count_mismatch(lab, couple):
    a, b = couple
    pts = [random_point()]
    sa = _committed_peer(lab, a, pts)
    msg = BlindedSet(blinded=[p.hex() for p in pts], **_meta_of(b))
    assert_abort(sa, lambda: sa.round2(msg), "COUNT_MISMATCH")


def test_metadata_resigned_by_other_key(lab, other_lab, couple):
    a, b = couple
    sa, sb, xa, xb = to_round1(lab, a, b)
    assert_abort(sa, lambda: sa.round2(resign(other_lab.seed, xb)), "BAD_SIGNATURE")


@pytest.mark.parametrize("field,value", [("count", 3), ("person_id", "mallory"),
                                         ("issued_at", "2030-01-01T00:00:00Z")])
def test_changed_signed_field(lab, couple, field, value):
    a, b = couple
    sa, sb, xa, xb = to_round1(lab, a, b)
    assert_abort(sa, lambda: sa.round2(xb.model_copy(update={field: value})), "BAD_SIGNATURE")


def test_wrong_lab_pubkey_on_receiver(lab, other_lab, couple):
    a, b = couple
    # Sam's credential comes from a different lab than the one Alex trusts.
    b_other = make_person(other_lab, "sam", ["CFTR", "HEXA"])
    sid = new_session_id()
    sa = Session("initiator", "detail", sid, a.credential, a.genes, lab.pubkey)
    sb = Session("joiner", "detail", sid, b_other.credential, b_other.genes, other_lab.pubkey)
    ca, cb = sa.round0(), sb.round0()
    sa.receive_commitment(cb); sb.receive_commitment(ca)
    sa.round1()
    assert_abort(sa, lambda: sa.round2(sb.round1()), "BAD_SIGNATURE")


def test_set_too_large(lab, couple):
    """21 points under metadata signed by the trusted lab key (bypassing issue())."""
    a, _ = couple
    pts = [random_point() for _ in range(21)]
    sa = _committed_peer(lab, a, pts)
    msg = BlindedSet(blinded=[p.hex() for p in pts], **_signed_meta(lab, 21))
    assert_abort(sa, lambda: sa.round2(msg), "SET_TOO_LARGE")


def test_point_changed_after_commit(lab, couple):
    a, b = couple
    sa, sb, xa, xb = to_round1(lab, a, b)
    pts = list(xb.blinded)
    pts[0] = random_point().hex()
    assert_abort(sa, lambda: sa.round2(xb.model_copy(update={"blinded": pts})),
                 "COMMITMENT_MISMATCH")


def test_round1_reordered_after_commit(lab, couple):
    a, b = couple
    sa, sb, xa, xb = to_round1(lab, a, b)
    pts = list(reversed(xb.blinded))
    assert_abort(sa, lambda: sa.round2(xb.model_copy(update={"blinded": pts})),
                 "COMMITMENT_MISMATCH")


def test_round1_replayed_from_previous_session(lab, couple):
    a, b = couple
    _, _, _, old_xb = to_round1(lab, a, b)
    sa, sb, xa, xb = to_round1(lab, a, b)
    assert_abort(sa, lambda: sa.round2(old_xb), "COMMITMENT_MISMATCH")


def _sam_messages(lab, a, b, sid):
    """Sam's honest round-0 and round-1 messages in session `sid`."""
    sa, sb = make_pair(lab, a, b, "detail", sid)
    cb = sb.round0()
    sb.receive_commitment(sa.round0())
    return cb, sb.round1()


def test_full_transcript_replay_from_previous_session(lab, couple):
    """Relay replays Sam's matching round 0 and round 1 from an older session."""
    a, b = couple
    old_cb, old_xb = _sam_messages(lab, a, b, "old-session")
    sa, _ = make_pair(lab, a, b, "detail", "new-session")
    sa.round0()
    sa.receive_commitment(old_cb)
    sa.round1()
    assert_abort(sa, lambda: sa.round2(old_xb), "COMMITMENT_MISMATCH")


def test_reflection_of_own_messages(lab, couple):
    a, b = couple
    sa, sb = make_pair(lab, a, b, "detail")
    ca = sa.round0()
    sa.receive_commitment(ca)     # relay reflects Alex's own commitment
    xa = sa.round1()
    assert_abort(sa, lambda: sa.round2(xa), "COMMITMENT_MISMATCH")


def test_cross_session_splice(lab, couple):
    """Relay feeds Alex a commitment and reveal from Sam's *other*, concurrent session."""
    a, b = couple
    cb2, xb2 = _sam_messages(lab, a, b, "session-2")
    sa, _ = make_pair(lab, a, b, "detail", "session-1")
    sa.round0()
    sa.receive_commitment(cb2)
    sa.round1()
    assert_abort(sa, lambda: sa.round2(xb2), "COMMITMENT_MISMATCH")


def test_mode_split(lab, couple):
    """Relay tells Alex 'detail' and Sam 'count'."""
    a, b = couple
    sid = new_session_id()
    sa = Session("initiator", "detail", sid, a.credential, a.genes, lab.pubkey)
    sb = Session("joiner", "count", sid, b.credential, b.genes, lab.pubkey)
    ca, cb = sa.round0(), sb.round0()
    sa.receive_commitment(cb); sb.receive_commitment(ca)
    xa, xb = sa.round1(), sb.round1()
    assert_abort(sa, lambda: sa.round2(xb), "COMMITMENT_MISMATCH")
    assert_abort(sb, lambda: sb.round2(xa), "COMMITMENT_MISMATCH")


def _committed_peer(lab, a, points, sid=None):
    """Alex in REVEALED state, having received an honest commitment to `points`."""
    sid = sid or new_session_id()
    sa = Session("initiator", "detail", sid, a.credential, a.genes, lab.pubkey)
    sa.round0()
    h = crypto.commit_blinded(points, session_id=sid, mode="detail", role="joiner")
    sa.receive_commitment({"h": h.hex()})
    sa.round1()
    return sa


def _signed_meta(lab, count):
    meta = {"person_id": "mallory", "count": count, "commitment": "00" * 32,
            "issued_at": "2026-10-24T09:00:00Z"}
    return {**meta, "lab_sig": crypto.sign(lab.seed, crypto.CREDENTIAL, meta)}


@pytest.mark.parametrize("bad", [bytes(32), b"\xff" * 32])
def test_invalid_point_in_round1(lab, couple, bad):
    a, _ = couple
    pts = [random_point(), bad]
    sa = _committed_peer(lab, a, pts)
    msg = BlindedSet(blinded=[p.hex() for p in pts], **_signed_meta(lab, 2))
    assert_abort(sa, lambda: sa.round2(msg), "INVALID_POINT")


def test_duplicate_points_in_round1(lab, couple):
    a, _ = couple
    p = random_point()
    sa = _committed_peer(lab, a, [p, p])
    msg = BlindedSet(blinded=[p.hex(), p.hex()], **_signed_meta(lab, 2))
    assert_abort(sa, lambda: sa.round2(msg), "INVALID_POINT")


def test_malformed_round1_payload(lab, couple):
    a, b = couple
    sa, sb, xa, xb = to_round1(lab, a, b)
    bad = xb.model_dump()
    bad["blinded"] = ["not-hex"]
    assert_abort(sa, lambda: sa.round2(bad), "MALFORMED_MESSAGE")


@pytest.mark.parametrize("value", ["2", True, 2.0])
def test_coerced_count_is_malformed(lab, couple, value):
    """Strict models: a count that is not a real int is rejected, not coerced."""
    a, b = couple
    sa, sb, xa, xb = to_round1(lab, a, b)
    bad = {**xb.model_dump(), "count": value}
    assert_abort(sa, lambda: sa.round2(bad), "MALFORMED_MESSAGE")


def test_unknown_field_is_malformed(lab, couple):
    a, b = couple
    sa, sb, xa, xb = to_round1(lab, a, b)
    bad = {**xb.model_dump(), "tokens": []}
    assert_abort(sa, lambda: sa.round2(bad), "MALFORMED_MESSAGE")


# --- round-2 checks ----------------------------------------------------------

def to_round2(lab, a, b, mode="detail"):
    sa, sb, xa, xb = to_round1(lab, a, b, mode)
    yb = sa.round2(xb)
    ya = sb.round2(xa)
    return sa, sb, ya, yb


@pytest.mark.parametrize("bad", [bytes(32), b"\xff" * 32])
def test_invalid_point_in_round2(lab, couple, bad):
    a, b = couple
    sa, _, ya, _ = to_round2(lab, a, b)
    pts = list(ya.points)
    pts[0] = bad.hex()
    assert_abort(sa, lambda: sa.result(DoubleBlinded(points=pts)), "INVALID_POINT")


def test_wrong_length_round2(lab, couple):
    a, b = couple
    sa, _, ya, _ = to_round2(lab, a, b)
    assert_abort(sa, lambda: sa.result(DoubleBlinded(points=ya.points[:-1])), "COUNT_MISMATCH")


def test_duplicate_points_in_round2(lab, couple):
    a, b = couple
    sa, _, ya, _ = to_round2(lab, a, b)
    pts = [ya.points[0]] * len(ya.points)
    assert_abort(sa, lambda: sa.result(DoubleBlinded(points=pts)), "INVALID_POINT")


# --- ordering ----------------------------------------------------------------

def test_round2_before_peer_commitment(lab, couple):
    a, b = couple
    sa, sb = make_pair(lab, a, b, "detail")
    sa.round0()
    sb.round0(); sb.receive_commitment({"h": "00" * 32})
    assert_abort(sa, lambda: sa.round2(sb.round1()), "OUT_OF_ORDER")


def test_round0_twice(lab, couple):
    a, b = couple
    sa, _ = make_pair(lab, a, b, "detail")
    sa.round0()
    assert_abort(sa, sa.round0, "OUT_OF_ORDER")


def test_result_before_round2(lab, couple):
    a, b = couple
    sa, sb, xa, xb = to_round1(lab, a, b)
    assert_abort(sa, lambda: sa.result(DoubleBlinded(points=[])), "OUT_OF_ORDER")


def test_no_calls_after_failure(lab, couple):
    a, b = couple
    sa, _ = make_pair(lab, a, b, "detail")
    sa.round0()
    with pytest.raises(ProtocolError):
        sa.round0()
    with pytest.raises(ProtocolError) as e:
        sa.receive_commitment({"h": "00" * 32})
    assert e.value.code == "OUT_OF_ORDER"


# --- documented limitations (no abort) ---------------------------------------

def test_forged_token_never_matches(lab):
    """S1 limitation: a forged point is not detected, but it cannot create a match.

    Mallory holds a real credential for HEXA only, and swaps her token for
    H(CFTR) (she cannot compute k_lab · H(CFTR)). The count is unchanged, so
    the protocol completes; Alex's CFTR does not match.
    """
    a = make_person(lab, "alex", ["CFTR"])
    m = make_person(lab, "mallory", ["HEXA"])
    sa, sm = make_pair(lab, a, m, "detail")
    sm._tokens = [crypto.hash_to_group("CFTR")]  # forged, bypassing the owner check
    ca, cm = sa.round0(), sm.round0()
    sa.receive_commitment(cm); sm.receive_commitment(ca)
    xa, xm = sa.round1(), sm.round1()
    ym = sa.round2(xm)
    ya = sm.round2(xa)
    ra, rm = sa.result(ya), sm.result(ym)
    assert ra.shared_count == 0 and rm.shared_count == 0


def test_round2_garbage_forces_false_no_match(lab):
    """DLEQ gap: valid but wrong round-2 points are not detected (documented)."""
    a = make_person(lab, "alex", ["CFTR"])
    b = make_person(lab, "sam", ["CFTR"])
    sa, sb, xa, xb = to_round1(lab, a, b)
    sa.round2(xb)
    garbage = DoubleBlinded(points=[random_point().hex()])
    r = sa.result(garbage)
    assert r.shared_count == 0  # a real match is hidden; no abort is possible here


def test_round2_echo_forces_false_match(lab):
    """DLEQ gap: echoing Alex's own round 2 back to him fakes a match (documented).

    The relay holds Alex's round 2 (a · X_peer), so it can do this without the
    partner. Nothing shared, yet Alex sees every gene matched, up to min(counts).
    """
    a = make_person(lab, "jordan", ["HBB", "SMN1"])
    b = make_person(lab, "riley", ["ASPA", "FANCC", "PAH"])
    for mode in ("detail", "count"):
        sa, sb, xa, xb = to_round1(lab, a, b, mode)
        own_round2 = sa.round2(xb)
        echoed = DoubleBlinded(points=own_round2.points[: len(xa.blinded)])
        r = sa.result(echoed)
        assert r.shared_count == 2  # truth: 0
        if mode == "detail":
            assert r.shared_genes == ["HBB", "SMN1"]


def test_round2_permutation_mislabels_genes(lab):
    """DLEQ gap: in Detail mode the peer knows which of Alex's slots match and can
    reorder its round 2, so Alex names the wrong shared gene (documented)."""
    a = make_person(lab, "casey", ["HBB", "HEXA", "SMN1"])
    b = make_person(lab, "morgan", ["GBA", "HBB", "SMN1"])
    # Two of Casey's three slots match. A cyclic shift moves every point, so the
    # one non-matching point always lands in a slot whose gene is then reported.
    sa, sb, xa, xb = to_round1(lab, a, b, "detail")
    sa.round2(xb)
    ya = sb.round2(xa)
    shifted = DoubleBlinded(points=ya.points[1:] + ya.points[:1])
    r = sa.result(shifted)
    assert r.shared_count == 2 and r.shared_genes != ["HBB", "SMN1"]  # wrong gene named
