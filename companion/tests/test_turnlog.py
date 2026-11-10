"""Every answered question is kept (question, game data, answer, tools, time) so prompts can be replayed and compared."""

from datetime import datetime

from foreverai.prompt import build_messages
from foreverai.turnlog import TurnLog


def test_turn_log_keeps_only_the_newest(tmp_path):
    log = TurnLog(tmp_path / "turns", keep=3)
    for i in range(5):
        log.save({"question": f"q{i}"})
    assert [r["question"] for r in log.load()] == ["q2", "q3", "q4"]
    assert len(list((tmp_path / "turns").glob("*.json"))) == 3


def test_turn_log_survives_a_broken_file(tmp_path):
    log = TurnLog(tmp_path / "turns")
    log.save({"question": "first"})
    (tmp_path / "turns" / "0000000000000-0.json").write_text("{not json", encoding="utf-8")
    assert [r["question"] for r in log.load()] == ["first"]


def test_history_tells_what_an_earlier_answer_did():
    history = [{"q": "Where do I turn it in?", "a": "Go to Dazalar.", "did": ["marked on the map: Dazalar (56.7, 59.5)"]},
               {"q": "ok", "a": "Good luck."}]
    msgs = build_messages("And now?", {}, history, [], now=datetime(2026, 11, 10, 20, 0), kb_date=None, hotfix_date=None)
    assert msgs[2]["content"] == "Go to Dazalar.\n[Done in this answer: marked on the map: Dazalar (56.7, 59.5)]"
    assert msgs[4]["content"] == "Good luck."


def test_question_is_repeated_after_the_game_data():
    """The game data is tens of thousands of English characters: the question and its language close the message."""
    msgs = build_messages("gdzie kupie coarse thread?", {"player": {"level": 11}}, [], [], now=datetime(2026, 11, 10, 20, 0),
                          kb_date=None, hotfix_date=None)
    user = msgs[-1]["content"]
    assert user.startswith("Player question: gdzie kupie coarse thread?")
    assert user.endswith('Now answer the player\'s question: "gdzie kupie coarse thread?"\n'
                         "Answer in the language the player wrote it in, not in the language of the game data.")


def test_a_long_conversation_keeps_twelve_turns_the_older_ones_short():
    """Six turns were too few ("ile razy mam sie o to samo pytac"); older answers are cut so that twelve stay cheap."""
    history = [{"q": f"q{i}", "a": f"answer {i} " + "x" * 600, "did": ["marked on the map: A (1.0, 2.0)"]}
               for i in range(14)]
    msgs = build_messages("now?", {}, history, [], now=datetime(2026, 11, 10, 20, 0), kb_date=None, hotfix_date=None)
    turns = msgs[1:-1]
    assert len(turns) == 24 and turns[0]["content"] == "q2"
    note = "\n[Done in this answer: marked on the map: A (1.0, 2.0)]"
    assert turns[1]["content"] == "answer 2 " + "x" * 391 + " […]" + note  # 400 characters of an older answer
    assert turns[-9]["content"].startswith("answer 9 ") and " […]" in turns[-9]["content"]
    assert turns[-7]["content"] == "answer 10 " + "x" * 600 + note  # the newest four in full
    assert turns[-1]["content"] == "answer 13 " + "x" * 600 + note
