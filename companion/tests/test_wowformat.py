import re

from foreverai.wowformat import md_to_wow


def test_heading_bullets_bold():
    out = md_to_wow("# Plan\n- **Kill** 7 [Young Nightsaber]\n- go to `Shadowglen`")
    lines = out.split("\n")
    assert lines[0] == "|cffffd100Plan|r"
    assert lines[1] == "• |cffffffffKill|r 7 |cff66ccff[Young Nightsaber]|r"
    assert lines[2] == "• go to |cffa0e0a0Shadowglen|r"


def test_color_codes_are_balanced():
    out = md_to_wow("# Your quest: **[The Balance of Nature]**\n**Task:** kill [Young Nightsaber]\nplain text")
    for line in out.split("\n"):
        assert line.count("|c") == line.count("|r"), line


def test_pipes_and_tables_are_safe():
    out = md_to_wow("| Model | Price |\n|---|---|\n| a | 1 |\ntext with | pipe")
    assert "|" not in out.replace("|c", "").replace("|r", "")
    assert "Model – Price" in out and "a – 1" in out


def _shown(out: str) -> str:
    """What the game shows of the text: links as their words, no colours."""
    out = re.sub(r"\|H.*?\|h(.*?)\|h", r"\1", out)
    return re.sub(r"\|c[0-9a-f]{8}|\|r", "", out)


def test_links_show_their_words_and_keep_the_address_for_copying():
    out = md_to_wow("See [wiki](https://warcraft.wiki.gg/wiki/X) and https://www.wowhead.com/forever/guide.")
    assert _shown(out) == "See wiki and www.wowhead.com."
    assert "|cff8fb8ff|Hforeverai:url:https://warcraft.wiki.gg/wiki/X|hwiki|h|r" in out
    # a full stop after a bare address is the sentence's, not the address's
    assert "|Hforeverai:url:https://www.wowhead.com/forever/guide|hwww.wowhead.com|h|r." in out
    for line in out.split("\n"):
        assert line.count("|c") == line.count("|r") and line.count("|H") * 2 == line.count("|h")


def test_an_address_is_not_formatted_inside():
    out = md_to_wow("odd https://example.com/a__b__c_d*e*/`x` and **bold** [Linen Cloth]")
    assert "|Hforeverai:url:https://example.com/a__b__c_d*e*/`x`|hexample.com|h" in out
    assert "|cffffffffbold|r" in out and "|cff66ccff[Linen Cloth]|r" in out
    # the words of a link lose their markdown, and a heading keeps the link in its colour
    assert _shown(md_to_wow("[**Wowhead** guide](https://a.example/g) end")) == "Wowhead guide end"
    head = md_to_wow("# Prices https://a.example/p?q=1&r=2, today")
    assert head == "|cffffd100Prices |cff8fb8ff|Hforeverai:url:https://a.example/p?q=1&r=2|ha.example|h|r, today|r"
