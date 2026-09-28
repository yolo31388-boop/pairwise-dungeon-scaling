"""副本动态难度与缩放系统 - 含8个bug"""
from dataclasses import dataclass, field

@dataclass
class Player:
    pid: str
    item_level: int = 100

@dataclass
class Boss:
    bid: str
    base_hp: float = 100000
    base_damage: float = 1000
    mechanics: list = field(default_factory=list)
    affix_level: int = 1

class DungeonScaling:
    def __init__(self):
        self.players: list[Player] = []
        self.base_trash_count = 10

    def scale_boss_hp(self, boss: Boss) -> float:
        # bug1: 只看人数不看装等
        count = len(self.players)
        return boss.base_hp * count

    def scale_boss_damage(self, boss: Boss) -> float:
        # bug2: 线性缩放
        count = len(self.players)
        return boss.base_damage * count

    def scale_trash_count(self) -> int:
        # bug3: 小怪数量不变
        return self.base_trash_count

    def scale_mechanics(self, boss: Boss, difficulty: str) -> list:
        # bug4: 难度不改变机制
        return boss.mechanics

    def scale_affix(self, boss: Boss, difficulty: str) -> int:
        # bug5: 词缀强度不变
        return boss.affix_level

    def recalc_on_player_leave(self, boss: Boss):
        # bug6: 有人退出不重算
        pass

    def scale_loot_ilevel(self, base_ilevel: int, difficulty: str) -> int:
        # bug7: 掉落不随难度提升
        return base_ilevel

    def get_effective_hp(self, boss: Boss) -> int:
        # bug8: 整数溢出
        return int(self.scale_boss_hp(boss))
