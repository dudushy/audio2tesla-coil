from coil_prep import feedback


def test_record_and_load_roundtrip_ignoring_corrupt_lines(tmp_path):
    feedback.record(tmp_path, {"stem": "other", "rating": 1})
    with (tmp_path / feedback.FILE).open("a") as f:
        f.write("{linha quebrada\n")
    feedback.record(tmp_path, {"stem": "vocals", "rating": -1, "tag": "picotado"})
    got = feedback.load(tmp_path)
    assert [e["stem"] for e in got] == ["other", "vocals"]


def test_suggest_stem_defaults_without_history():
    assert feedback.suggest_stem([]) == "other"


def test_suggest_stem_prefers_best_average_and_ignores_all_negative():
    good = [{"stem": "vocals", "rating": 1}, {"stem": "vocals", "rating": 1}]
    bad_other = [{"stem": "other", "rating": -1}]
    assert feedback.suggest_stem(good + bad_other) == "vocals"
    assert feedback.suggest_stem([{"stem": "vocals", "rating": -1}]) == "other"


from coil_prep.notes import EditNote


def test_diff_notes_classifies_deleted_added_moved_and_kept():
    gen = [
        EditNote(0, 0.0, 0.5, 60),
        EditNote(1, 0.5, 1.0, 64),
        EditNote(2, 1.0, 1.5, 67),
        EditNote(3, 1.5, 2.0, 72),
    ]
    fin = [
        EditNote(0, 0.0, 0.5, 60),  # igual
        EditNote(2, 1.1, 1.6, 69),  # movida: +0,1 s e +2 semitons
        EditNote(3, 1.5, 2.0004, 72),  # < 1 ms: continua igual
        EditNote(9, 3.0, 3.2, 70),  # nova
    ]
    d = feedback.diff_notes(gen, fin)
    assert d["summary"] == {
        "generated": 4,
        "final": 4,
        "kept": 2,
        "deleted": 1,
        "added": 1,
        "moved": 1,
    }
    assert d["deleted"] == [[0.5, 1.0, 64]]
    assert d["added"] == [[3.0, 3.2, 70]]
    assert d["moved"] == [
        {
            "from": [1.0, 1.5, 67],
            "to": [1.1, 1.6, 69],
            "pitch_delta": 2,
            "start_delta": 0.1,
            "end_delta": 0.1,
        }
    ]
    assert feedback.has_changes(d)
    assert not feedback.has_changes(feedback.diff_notes(gen, gen))


def test_count_ratings_ignores_edit_events():
    assert (
        feedback.count_ratings([{"rating": 1}, {"kind": "edit"}, {"rating": -1}, {"rating": None}])
        == 2
    )
