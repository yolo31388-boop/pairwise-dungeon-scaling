"""副本动态难度与缩放系统 - 红态测试"""
import pytest, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dungeon.scaling import DungeonScaling, Player, Boss

class TestHpScaling:
    def test_hp_considers_item_level(self):
        ds = DungeonScaling()
        ds.players = [Player("p1", 100), Player("p2", 100)]
        boss = Boss("b1", base_hp=100000)
        hp_normal = ds.scale_boss_hp(boss)
        ds.players = [Player("p1", 200), Player("p2", 200)]
        hp_high = ds.scale_boss_hp(boss)
        assert hp_high > hp_normal  # bug1: 装等高应该BOSS血更多

class TestDamageScaling:
    def test_damage_uses_sqrt_not_linear(self):
        ds = DungeonScaling()
        ds.players = [Player("p1", 100)]
        boss = Boss("b1", base_damage=1000)
        dmg1 = ds.scale_boss_damage(boss)
        ds.players = [Player(f"p{i}", 100) for i in range(10)]
        dmg10 = ds.scale_boss_damage(boss)
        # 10人伤害不应是1人的10倍，应该约3倍(sqrt)
        assert dmg10 < dmg1 * 5  # bug2: 线性10倍

class TestTrashScaling:
    def test_trash_count_scales_with_players(self):
        ds = DungeonScaling()
        ds.players = [Player("p1", 100)]
        c1 = ds.scale_trash_count()
        ds.players = [Player(f"p{i}", 100) for i in range(10)]
        c10 = ds.scale_trash_count()
        assert c10 > c1  # bug3: 都是10

class TestMechanicsByDifficulty:
    def test_harder_difficulty_adds_mechanics(self):
        ds = DungeonScaling()
        boss = Boss("b1", mechanics=["enrage"])
        normal = ds.scale_mechanics(boss, "normal")
        heroic = ds.scale_mechanics(boss, "heroic")
        assert len(heroic) > len(normal)  # bug4: 一样

class TestAffixScaling:
    def test_affix_level_increases_with_difficulty(self):
        ds = DungeonScaling()
        boss = Boss("b1", affix_level=1)
        l1 = ds.scale_affix(boss, "normal")
        l3 = ds.scale_affix(boss, "mythic")
        assert l3 > l1  # bug5: 都是1

class TestRecalcOnLeave:
    def test_player_leave_triggers_recalc(self):
        ds = DungeonScaling()
        ds.players = [Player(f"p{i}", 100) for i in range(5)]
        boss = Boss("b1", base_hp=100000)
        hp_before = ds.scale_boss_hp(boss)
        ds.players.pop()  # 一人退出
        ds.recalc_on_player_leave(boss)
        hp_after = ds.scale_boss_hp(boss)
        assert hp_after < hp_before  # bug6: 没重算

class TestLootScaling:
    def test_loot_ilevel_increases_with_difficulty(self):
        ds = DungeonScaling()
        l_normal = ds.scale_loot_ilevel(100, "normal")
        l_mythic = ds.scale_loot_ilevel(100, "mythic")
        assert l_mythic > l_normal  # bug7: 都是100

class TestNoOverflow:
    def test_large_party_no_overflow(self):
        ds = DungeonScaling()
        ds.players = [Player(f"p{i}", 1000) for i in range(25)]
        boss = Boss("b1", base_hp=10**9)
        hp = ds.get_effective_hp(boss)
        assert hp > 0  # bug8: 溢出变负数
