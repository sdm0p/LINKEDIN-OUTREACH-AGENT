from app.services.keyword_service import select_for_run


def _kw(i, *, pinned=False, active=True, last_used=None, times_used=0):
    return {
        "id": i,
        "text": f"kw{i}",
        "tier": "skill",
        "pinned": pinned,
        "active": active,
        "last_used_at": last_used,
        "times_used": times_used,
        "created_at": "2026-01-01T00:00:00+00:00",
    }


def test_empty_pool_selects_nothing():
    assert select_for_run([], limit=5) == []


def test_respects_limit():
    rows = [_kw(i) for i in range(10)]
    assert len(select_for_run(rows, limit=5)) == 5


def test_pinned_always_included_first():
    rows = [_kw(1), _kw(2, pinned=True), _kw(3), _kw(4, pinned=True)]
    picked = select_for_run(rows, limit=2)
    assert [r["id"] for r in picked] == [2, 4]


def test_pinned_displaces_unpinned():
    rows = [_kw(1), _kw(2), _kw(3, pinned=True)]
    picked = select_for_run(rows, limit=1)
    assert [r["id"] for r in picked] == [3]


def test_never_used_before_most_recently_used():
    rows = [
        _kw(1, last_used="2026-01-01T00:00:00+00:00", times_used=3),
        _kw(2, last_used=None, times_used=0),
        _kw(3, last_used="2026-01-02T00:00:00+00:00", times_used=1),
    ]
    picked = select_for_run(rows, limit=3)
    assert [r["id"] for r in picked] == [2, 1, 3]


def test_tiebreak_on_times_used():
    rows = [
        _kw(1, last_used="2026-01-01T00:00:00+00:00", times_used=5),
        _kw(2, last_used="2026-01-01T00:00:00+00:00", times_used=1),
    ]
    picked = select_for_run(rows, limit=2)
    assert [r["id"] for r in picked] == [2, 1]


def test_inactive_never_selected():
    rows = [_kw(1, active=False), _kw(2, active=False)]
    assert select_for_run(rows, limit=5) == []


def test_inactive_pinned_not_selected():
    rows = [_kw(1, pinned=True, active=False), _kw(2)]
    picked = select_for_run(rows, limit=2)
    assert [r["id"] for r in picked] == [2]
