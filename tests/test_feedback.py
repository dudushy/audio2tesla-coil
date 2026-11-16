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
