"""Selling to a vendor from a proposal card: the AI hears how the sale ended, not how it stood a moment after the
click. In the add-on simulator, with a bag and a vendor made up here."""

import pytest

from addonsim import Sim, install_game

pytest.importorskip("lupa")

# thirteen stacks worth 10c, 20c ... 130c; the game sells a stack a moment after the click (the slot is locked until
# the server answers), and the add-on clicks them one after another
VENDOR = r"""
MerchantFrame = { open = true }
function MerchantFrame:IsShown() return self.open end
TEST_money = 1000
GetMoney = function() return TEST_money end
TEST_bag, TEST_items = {}, {}
for i = 1, 13 do
  TEST_bag[i] = { itemID = 100 + i, price = 10 * i }
  TEST_items[i] = { id = 100 + i, name = "[Junk " .. i .. "]", count = 1, price = (10 * i) .. "c" }
end
C_Container.GetContainerNumSlots = function(bag) return bag == 0 and 16 or 0 end
C_Container.GetContainerItemInfo = function(bag, slot)
  local it = bag == 0 and TEST_bag[slot]
  return it and { itemID = it.itemID, isLocked = it.locked or false, stackCount = 1 } or nil
end
C_Container.UseContainerItem = function(bag, slot)
  local it = TEST_bag[slot]
  if not it or it.kept then return end
  it.locked = not TEST_slow
  C_Timer.After(TEST_slow and 0.9 or 0.4, function() TEST_bag[slot] = nil TEST_money = TEST_money + it.price end)
end
local ask = ForeverAI.Ask
TEST_asked = {}
ForeverAI.Ask = function(text, opts)
  local left = 0
  for _ in pairs(TEST_bag) do left = left + 1 end
  TEST_asked[#TEST_asked + 1] = left .. " left: " .. text
  return ask(text, opts)
end
ForeverAI.UI.UpdateAnswer({ final = true, msgId = 5, text = "Sell these.", actions = {
  { id = 1, addon = "Vendor", op = "vendor_sell", total = "9s 10c", items = TEST_items } } })
"""
ASKED = "return table.concat(TEST_asked, ' | ')"


@pytest.fixture
def sim(tmp_path):
    install_game(tmp_path / "game", pool=40)
    s = Sim(tmp_path / "game").start()
    s.slash("/fai")
    yield s
    s.close()


def text(value) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else value


def test_the_ai_hears_about_a_sale_when_it_is_over(sim):
    """"Przy sprzedaży jakby nie odświeżał": the AI got the bags a second after the click, with half of the stacks
    still there, called the sale partial and proposed to sell the rest, which was gone by then."""
    sim.lua(VENDOR)
    sim.click("Accept")
    sim.run(1.5)
    assert text(sim.lua(ASKED)) == "" and "Selling…" in " | ".join(sim.texts())  # still selling: nothing to tell yet
    assert "Undo" not in sim.clickables()  # buying back while the rest is being sold would undo half of it
    sim.run(6)
    assert text(sim.lua(ASKED)) == ("0 left: [Player action] Accepted: sell 13 item types for 9s 10c — "
                                    "done: 13 of 13 stacks sold for 9s 10c.")
    assert "Sold 13 of 13 stacks for 9s 10c." in " | ".join(sim.texts()) and sim.errors == []
    assert "Undo" in sim.clickables()


def test_a_game_that_answers_slowly_and_locks_nothing_is_waited_for(sim):
    """A stack the game has not answered yet is not one the vendor did not take."""
    sim.lua("TEST_slow = true")
    sim.lua(VENDOR)
    sim.click("Accept")
    sim.run(9)
    assert text(sim.lua(ASKED)) == ("0 left: [Player action] Accepted: sell 13 item types for 9s 10c — "
                                    "done: 13 of 13 stacks sold for 9s 10c.")


def test_a_stack_the_vendor_does_not_take_is_told(sim):
    sim.lua(VENDOR)
    sim.lua("TEST_bag[13].kept = true")
    sim.click("Accept")
    sim.run(9)
    assert text(sim.lua(ASKED)) == ("1 left: [Player action] Accepted: sell 13 item types for 9s 10c — "
                                    "12 of 13 stacks sold for 7s 80c; the vendor did not take the rest.")
    assert sim.errors == []


def test_a_sale_cut_short_by_closing_the_window_is_told(sim):
    sim.lua(VENDOR)
    sim.click("Accept")
    sim.run(0.5)
    sim.lua("MerchantFrame.open = false")
    sim.run(8)
    (said,) = text(sim.lua(ASKED)).split(" | ")
    assert said.endswith("stacks sold for 60c; the vendor window was closed.") and " of 13 stacks" in said
    assert not said.startswith("0 left") and sim.errors == []


def test_nothing_to_sell_fails_at_once(sim):
    sim.lua(VENDOR)
    sim.lua("TEST_bag = {}")
    sim.click("Accept")
    sim.run(3)
    assert text(sim.lua(ASKED)) == ("0 left: [Player action] Accepted: sell 13 item types for 9s 10c — "
                                    "failed: These items are no longer in your bags..")
