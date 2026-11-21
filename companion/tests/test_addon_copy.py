"""Copying text out of the add-on's window. The game gives an add-on the clipboard only through a text field, so the
Copy button of a message (shown while the mouse is over it) opens the message's text in one, selected, and Ctrl+C
copies it. These run the whole add-on in the add-on simulator; the answers come as the app writes them."""

from datetime import datetime

from addonsim import Sim, install_game

from addonsim.sim import _b, _pair

from foreverai.wowformat import md_to_wow, url_link

CLOCK = datetime(2026, 11, 10, 12, 0)
FIELD = "Ask… (Alt+click an item, spell or quest to tag it)"
LINEN = "|cff9d9d9d|Hitem:2589::::::::24:::::::::|h[Linen Cloth]|h|r"  # an item the player tagged with Alt+click

RECT = "local f = ... return f:GetLeft(), f:GetTop(), f:GetRight(), f:GetBottom()"
BOX_SHOWN = "return ForeverAICopyFrame ~= nil and ForeverAICopyFrame:IsShown()"


def _start(tmp_path):
    install_game(tmp_path / "game", pool=40)
    sim = Sim(tmp_path / "game", clock=CLOCK).start()
    sim.slash("/fai")
    return sim


def _answer(sim, question, markdown, **extra):
    """Asks the question and delivers the app's answer to it."""
    sim.ask(question)
    sim.run(1)
    q = sim.sent_messages()[-1]
    assert q["type"] == "question"
    sim.deliver({"kind": "answer", "final": True, "msgId": q["msgId"], "text": md_to_wow(markdown),
                 "model": "sim/model", "secs": 1.2, **extra})
    sim.run(2)


def _message(sim, text):
    """The chat message (its frame) that shows this text."""
    fs, _ = _pair(sim._wow.findText(_b(text), 1))
    assert fs is not None, f"no message shows {text!r}"
    return sim._wow.S[fs].parent


def _copy_button(sim):
    w, _ = _pair(sim._wow.find(b"Copy", 1))
    assert w is not None, "no Copy button is shown"
    return w


def _copy(sim, text):
    """Moves the mouse over the message, clicks its Copy button and presses Ctrl+C; returns the clipboard."""
    sim.hover(text)
    sim.click("Copy")
    sim.key("CTRL-C")
    return sim.clipboard


def test_a_question_is_copied_with_its_copy_button_and_pasted_into_the_field(tmp_path):
    """"Chcę skopiować mój poprzedni prompt, ale nie mogę, muszę przepisać"."""
    sim = _start(tmp_path)
    _answer(sim, f"How much is {LINEN} worth?", "About **8 silver** a stack.")
    question = "How much is [Linen Cloth] worth?"
    assert "Copy" not in sim.clickables()  # only while the mouse is over a message
    sim.hover(question)
    assert "Copy" in sim.clickables()
    # beside the player's own question, not over its words
    left, top, right, bottom = sim.lua(RECT, _copy_button(sim))
    m_left, m_top, _, m_bottom = sim.lua(RECT, _message(sim, question))
    assert right <= m_left and m_bottom < top <= m_top + 1
    sim.click("Copy")
    # the box: the question as the chat shows it, all of it selected, in a field that has the keyboard
    assert sim.lua(BOX_SHOWN) is True
    assert sim.selected_text() == question
    assert "Ctrl+C copies the selected text" in sim.texts()
    assert "Copy" not in sim.clickables()  # (the button is gone while the box is open)
    # what is typed does not change the text, and it is selected again
    sim.type("xyz")
    assert sim.selected_text() == question
    sim.key("CTRL-C")
    assert sim.clipboard == question
    assert "Copied" in sim.texts()
    sim.run(1)  # the box closes by itself and the window says so
    assert sim.lua(BOX_SHOWN) is False
    assert "Copied to the clipboard." in sim.texts()
    assert sim._wow.focusedEditBox() is None  # the keys went back to the game
    # pasted into the text field, it is the question again, to change and send
    sim.click(FIELD)
    sim.key("CTRL-V")
    assert sim.lua("local b = ... return b:GetText()", sim._wow.focusedEditBox()) == _b(question)
    assert sim.errors == []
    sim.close()


def test_the_copy_button_beside_a_question_stays_while_the_mouse_goes_to_it(tmp_path):
    """"Przycisk copy wyświetla się obok boxa z moim promptem, ale przy próbie najechania znika, bo wychodzę myszką za
    obszar boxa"."""
    sim = _start(tmp_path)
    question = "where is the inn?"
    _answer(sim, question, "In Auberdine, by the docks.")
    sim.hover(question)
    left, top, right, bottom = sim.lua(RECT, _copy_button(sim))
    m_left = sim.lua(RECT, _message(sim, question))[0]
    y = (top + bottom) / 2
    # slowly from the question to its button, through the gap between them, stopping on the way
    for x in (m_left + 2, (right + m_left) / 2, right - 2, (left + right) / 2):
        sim.point(x, y)
        sim.run(0.6)
        assert "Copy" in sim.clickables(), f"the button went with the mouse at x={x:.0f}"
    sim.click("Copy")
    assert sim.selected_text() == question
    sim.key("ESCAPE")
    # off the question and its button the button goes, after a moment to come back
    sim.hover(question)
    sim.point(left - 40, y)
    sim.run(0.1)
    assert "Copy" in sim.clickables()
    sim.run(0.5)
    assert "Copy" not in sim.clickables()
    assert sim.errors == []
    sim.close()


def test_an_answer_is_copied_as_text_with_its_web_addresses_whole(tmp_path):
    sim = _start(tmp_path)
    md = ("## Where to sell\n\nSell your [Linen Cloth] at the auction house; see "
          "[the price history](https://www.wowhead.com/classic/item=2589/linen-cloth#prices) or "
          "https://warcraft.wiki.gg/wiki/Linen_Cloth.\n\n- two stacks sell faster than one")
    _answer(sim, "Where do I sell cloth?", md)
    # the chat shows the sites and the links' words, not the addresses
    shown = " ".join(sim.texts())
    assert "warcraft.wiki.gg" in shown and "the price history" in shown and "https://" not in shown
    sim.hover("Sell your [Linen Cloth]")
    # in the answer's bottom right corner, where its last line leaves room
    left, top, right, bottom = sim.lua(RECT, _copy_button(sim))
    a_left, a_top, a_right, a_bottom = sim.lua(RECT, _message(sim, "Sell your [Linen Cloth]"))
    assert a_left < left < right <= a_right and a_bottom < bottom < top < a_top and bottom - a_bottom <= 6
    assert _copy(sim, "Sell your [Linen Cloth]") == (
        "Where to sell\n\nSell your [Linen Cloth] at the auction house; see the price history "
        "(https://www.wowhead.com/classic/item=2589/linen-cloth#prices) or https://warcraft.wiki.gg/wiki/Linen_Cloth."
        "\n\n• two stacks sell faster than one")
    # the line under the answer (the model, the time) is not a message: no button over it
    sim.run(1)
    sim.hover("model  ·  1.2 s")
    sim.run(0.5)  # (the answer's button stays a moment after the mouse left it)
    assert "Copy" not in sim.clickables()
    assert sim.errors == []
    sim.close()


LINK = "local frame, link, script = ... frame:GetScript(script)(frame, link, 'text', 'LeftButton')"


def test_a_click_on_a_web_address_copies_it(tmp_path):
    """The game opens no web pages: a link to one shows its address and copies it, in the answer and in its sources."""
    sim = _start(tmp_path)
    sources = [{"title": "Linen Cloth - Item - Wowhead", "url": "https://www.wowhead.com/classic/item=2589",
                "date": "2026-11-01"}]
    _answer(sim, "linen?", "Look here: https://warcraft.wiki.gg/wiki/Linen_Cloth", sources=sources)
    answer = _message(sim, "Look here:")
    address = "https://warcraft.wiki.gg/wiki/Linen_Cloth"
    sim.lua(LINK, answer, "foreverai:url:" + address, "OnHyperlinkEnter")
    assert address in sim.texts() and "Click to copy the address" in sim.texts()  # the tooltip
    sim.lua(LINK, answer, "foreverai:url:" + address, "OnHyperlinkClick")
    assert sim.lua(BOX_SHOWN) is True and sim.selected_text() == address
    sim.key("ESCAPE")
    # the sources under the answer: each title is a link that keeps its address
    line = _message(sim, "Sources: Linen Cloth - Item - Wowhead")
    text = sim.lua("local f = ... return f.fs:GetText()", line).decode()
    assert "|Hforeverai:url:https://www.wowhead.com/classic/item=2589|hLinen Cloth - Item - Wowhead|h" in text
    sim.lua(LINK, line, "foreverai:url:https://www.wowhead.com/classic/item=2589", "OnHyperlinkClick")
    sim.key("CTRL-C")
    assert sim.clipboard == "https://www.wowhead.com/classic/item=2589"
    assert sim.errors == []
    sim.close()


def test_every_kind_of_message_copies_and_an_unfinished_answer_does_not(tmp_path):
    sim = _start(tmp_path)
    sim.ask("what now")
    sim.run(1)
    q = sim.sent_messages()[-1]
    sim.deliver({"kind": "answer", "final": False, "msgId": q["msgId"], "text": "Half of the ans", "phase": "Thinking…"})
    sim.run(2)
    sim.hover("Half of the ans")
    assert "Copy" not in sim.clickables()  # the answer is still being written
    sim.deliver({"kind": "answer", "final": True, "msgId": q["msgId"], "text": "Half of the answer, and the rest.",
                 "model": "sim/model"})
    sim.run(2)
    assert _copy(sim, "Half of the answer") == "Half of the answer, and the rest."
    sim.run(1)
    sim.lua("ForeverAI.UI.AddSystemNote('[auto] Quest accepted: |cffffff00[The Fragments Within]|r')")
    sim.lua("ForeverAI.UI.RouteProgress({ 'turn in The Fragments Within' }, 'Gwennyth Bly\\'Leggonde', 120)")
    sim.lua("ForeverAI.UI.BeginAnswer(99) ForeverAI.UI.FailAnswer('The app did not answer in time.')")
    sim.run(1)
    assert _copy(sim, "Quest accepted") == "[auto] Quest accepted: [The Fragments Within]"
    sim.run(1)
    line = "Done: turn in The Fragments Within. Next: Gwennyth Bly'Leggonde (120 m)"  # (the screen says it too)
    assert _copy(sim, line) == line
    sim.run(1)
    assert _copy(sim, "did not answer in time") == "The app did not answer in time."
    assert sim.errors == []
    sim.close()


def test_the_copy_box_closes_with_esc_or_a_click_beside_it_and_old_messages_copy_too(tmp_path):
    sim = _start(tmp_path)
    _answer(sim, "first question", "First answer.")
    sim.hover("first question")
    sim.click("Copy")
    sim.key("ESCAPE")  # closes the box, not the window
    assert sim.lua(BOX_SHOWN) is False and sim.lua("return ForeverAIFrame:IsShown()") is True
    assert sim._wow.focusedEditBox() is None
    sim.hover("First answer.")
    sim.click("Copy")
    # a click into the text puts the cursor there and ends the selection; Select all selects it again
    sim.lua("ForeverAICopyFrame.edit:SetCursorPosition(3)")
    assert sim.selected_text() is None
    sim.click("Select all")
    assert sim.selected_text() == "First answer."
    sim.lua("ForeverAICopyFrame:GetScript('OnMouseDown')(ForeverAICopyFrame, 'LeftButton')")  # a click beside it
    assert sim.lua(BOX_SHOWN) is False
    # closed with the window, it does not come back when the window opens again
    sim.hover("first question")
    sim.click("Copy")
    sim.slash("/fai")
    assert sim.lua("return ForeverAIFrame:IsShown()") is False and sim._wow.focusedEditBox() is None
    sim.slash("/fai")
    assert sim.lua("return ForeverAIFrame:IsShown()") is True and sim.lua(BOX_SHOWN) is False
    # after a /reload the conversation is shown again from the saved variables, and copies as before
    sim.reload()
    sim.slash("/fai")
    assert _copy(sim, "first question") == "first question"
    sim.run(1)
    assert _copy(sim, "First answer.") == "First answer."
    assert sim.errors == []
    sim.close()


def test_the_copy_button_of_a_long_answer_stays_in_sight_and_messages_take_no_clicks(tmp_path):
    sim = _start(tmp_path)
    long = "\n\n".join(f"Paragraph {i}: " + "words " * 40 for i in range(12))
    _answer(sim, "tell me everything", long)
    answer = _message(sim, "Paragraph 11:")
    chat = sim.lua("local f = ... return f:GetParent():GetParent()", answer)  # the scroll frame of the chat
    c_left, c_top, c_right, c_bottom = sim.lua(RECT, chat)
    # the chat shows the end of the answer (its top is above what can be seen): the button is at the answer's bottom
    sim.hover("Paragraph 11:")
    _, a_top, _, a_bottom = sim.lua(RECT, answer)
    left, top, right, bottom = sim.lua(RECT, _copy_button(sim))
    assert a_top > c_top and c_bottom <= a_bottom < bottom <= a_bottom + 6 and c_left < left < right <= c_right
    # the messages take no mouse themselves: what they cover when scrolled out of sight keeps its clicks
    assert sim.lua("local f = ... return f:IsMouseEnabled()", answer) is False
    # the box shows a long text in a scroll area
    sim.click("Copy")
    assert sim.selected_text() == md_to_wow(long)
    before = sim.lua("return ForeverAICopyFrame.scroll:GetVerticalScroll()")
    sim.wheel(-3, 2)  # (the chat's scroll area is the first)
    assert sim.lua("return ForeverAICopyFrame.scroll:GetVerticalScroll()") > before
    box_top, box_bottom = sim.lua("local b = ForeverAICopyFrame.box return b:GetTop(), b:GetBottom()")
    window_top, window_bottom = sim.lua("return ForeverAIFrame:GetTop(), ForeverAIFrame:GetBottom()")
    assert window_bottom < box_bottom < box_top < window_top
    sim.key("ESCAPE")
    # scrolled to the top, the answer's bottom is out of sight below: the button stays at the bottom of the chat
    for _ in range(40):
        sim.wheel(1)
    sim.hover("Paragraph 0:")
    a_bottom = sim.lua(RECT, answer)[3]
    left, top, right, bottom = sim.lua(RECT, _copy_button(sim))
    assert a_bottom < c_bottom < bottom <= c_bottom + 6 and top < c_top and c_left < left < right <= c_right
    sim.leave()  # the mouse leaves the window
    sim.run(0.5)
    assert "Copy" not in sim.clickables()
    assert sim.errors == []
    sim.close()


PLAIN = "return ForeverAI.UI.PlainText(...)"


def test_the_text_copied_is_what_the_chat_shows(tmp_path):
    sim = _start(tmp_path)
    cases = {
        LINEN + " x3": "[Linen Cloth] x3",
        "|cffffd100Plan|r\n• |cffffffffKill|r 7 |cff66ccff[Young Nightsaber]|r": "Plan\n• Kill 7 [Young Nightsaber]",
        "|TInterface\\AddOns\\ForeverAI\\media\\route-auto:14:14|t Route updated": "Route updated",
        url_link("https://a.example/page?x=1", "a.example"): "https://a.example/page?x=1",
        url_link("https://a.example/page", "the guide"): "the guide (https://a.example/page)",
        "Sources: |Hforeverai:url:https://b.example/x|hB title|h |cff6a7380(b.example)|r": "Sources: B title (https://b.example/x) (b.example)",
        "  spaced out \n": "spaced out",
    }
    for raw, want in cases.items():
        assert sim.lua(PLAIN, raw).decode() == want, raw
    assert sim.errors == []
    sim.close()
