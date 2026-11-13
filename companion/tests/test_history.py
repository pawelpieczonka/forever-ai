"""The conversation as the model gets it: what earlier answers did, the question at the end, twelve turns."""

from datetime import datetime

from foreverai.prompt import build_messages


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


def test_a_long_conversation_keeps_twelve_turns_whole_within_a_budget():
    """Six turns were too few ("ile razy mam sie o to samo pytac"); and of twelve, the older answers cut to 400
    characters lost what had been said ("ignoruje połowę swoich poprzednich wiadomości"). Twelve turns go along, the
    answers whole as far as a budget of text reaches, the newest first; beyond it an answer is cut to its beginning."""
    history = [{"q": f"q{i}", "a": f"answer {i} " + "x" * 600, "did": ["marked on the map: A (1.0, 2.0)"]}
               for i in range(14)]
    msgs = build_messages("now?", {}, history, [], now=datetime(2026, 11, 10, 20, 0), kb_date=None, hotfix_date=None)
    turns = msgs[1:-1]
    assert len(turns) == 24 and turns[0]["content"] == "q2"
    note = "\n[Done in this answer: marked on the map: A (1.0, 2.0)]"
    assert turns[1]["content"] == "answer 2 " + "x" * 600 + note  # whole: twelve such answers are within the budget
    assert turns[-1]["content"] == "answer 13 " + "x" * 600 + note
    # answers too long for the budget: the newest whole, the oldest cut to 400 characters
    long = [{"q": f"q{i}", "a": f"answer {i} " + "x" * 5000} for i in range(14)]
    turns = build_messages("now?", {}, long, [], now=datetime(2026, 11, 10, 20, 0), kb_date=None, hotfix_date=None)[1:-1]
    assert turns[1]["content"] == "answer 2 " + "x" * 391 + " […]"
    assert turns[-1]["content"] == "answer 13 " + "x" * 5000 and turns[-7]["content"] == "answer 10 " + "x" * 5000


def test_messages_of_the_add_on_do_not_push_the_players_own_out_of_sight():
    """A dozen route updates in a few minutes: what the player said before them still goes along, and the old
    answers to the add-on are cut to their first lines."""
    history = [{"q": "dodaj flymasterow do trasy", "a": "Dodani."}]
    history += [{"q": f"[Quest log changed] took [Q{i}]. Update the route on the map.", "a": f"Route {i}. " + "y" * 500}
                for i in range(14)]
    history += [{"q": "co dalej?", "a": "Idź do Wileya."}]
    msgs = build_messages("i co teraz?", {}, history, [], now=datetime(2026, 11, 12, 21, 0), kb_date=None, hotfix_date=None)
    turns = msgs[1:-1]
    assert turns[0]["content"] == "dodaj flymasterow do trasy" and len(turns) == 32
    assert turns[3]["content"] == "Route 0. " + "y" * 251 + " […]"  # an old answer to the add-on: 260 characters
    assert turns[-3]["content"].startswith("Route 13. ") and len(turns[-3]["content"]) > 500  # the newest in full


def test_the_notes_and_the_plan_come_before_the_game_data():
    msgs = build_messages("co dalej?", {"player": {"level": 18}, "mapSizes": ["Westfall: 3200 x 2134"]}, [], [],
                          now=datetime(2026, 11, 12, 21, 0), kb_date=None, hotfix_date=None,
                          notes="Corrections:\n- No flight to Thelsamar.",
                          route_plan={"skip": ["Twisted Hatred"], "use": ["Moonstalker Sire"], "avoid": ["Thorgrum Borrelson"],
                                      "then": [{"zone": "Redridge Mountains"}]})
    user = msgs[-1]["content"]
    assert user.index("# Notes of this conversation") < user.index("# How the route on the map was planned") < user.index("# Current game state")
    assert "- No flight to Thelsamar." in user and "- left out (skip): [Twisted Hatred]" in user
    assert "- objectives from (use): Moonstalker Sire" in user and "- ways not to take (avoid): Thorgrum Borrelson" in user
    assert "- goes on into (then): Redridge Mountains" in user
    assert "mapSizes" not in user and "3200 x 2134" not in user  # the planner's own data is not read by the model
