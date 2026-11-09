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


def test_links_become_text():
    out = md_to_wow("See [wiki](https://warcraft.wiki.gg/wiki/X) and https://www.wowhead.com/forever/guide")
    assert "wiki" in out and "www.wowhead.com" in out and "https://" not in out
