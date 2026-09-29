"""副本动态难度与缩放系统。

修复点：
1. BOSS血量同时考虑人数与队伍平均装等；
2. BOSS伤害按人数平方根缩放，避免线性膨胀；
3. 小怪数量按人数区间配置；
4. 难度提升同步升级技能机制与技能参数；
5. 精英怪词缀强度随难度提升；
6. 成员变化触发重算，带过渡保护（单次属性变化有上限）；
7. 掉落装等按难度系数映射；
8. 所有数值按64位有符号整数饱和，杜绝整数溢出。
"""
import math
from dataclasses import dataclass, field

INT64_MAX = 2 ** 63 - 1
INT64_MIN = -(2 ** 63)

BASE_ITEM_LEVEL = 100

DIFFICULTIES = ("normal", "heroic", "mythic")

# 每个难度的配置：
# hp/damage_multiplier: 难度本身带来的强度系数
# mechanics:           该难度新增的技能机制
# mechanic_power:      单个技能参数的威力倍率
# affix_bonus:         在基础词缀等级上的增量
# loot_multiplier:     掉落装等系数
DIFFICULTY_CONFIG = {
    "normal": {
        "hp_multiplier": 1.0,
        "damage_multiplier": 1.0,
        "mechanics": [],
        "mechanic_power": 1.0,
        "affix_bonus": 0,
        "loot_multiplier": 1.0,
    },
    "heroic": {
        "hp_multiplier": 1.2,
        "damage_multiplier": 1.15,
        "mechanics": ["extra_add", "tank_buster"],
        "mechanic_power": 1.5,
        "affix_bonus": 1,
        "loot_multiplier": 1.15,
    },
    "mythic": {
        "hp_multiplier": 1.5,
        "damage_multiplier": 1.3,
        "mechanics": ["extra_add", "tank_buster", "enrage_timer", "shared_damage"],
        "mechanic_power": 2.0,
        "affix_bonus": 2,
        "loot_multiplier": 1.3,
    },
}

# 小怪数量按人数区间配置：(人数下限, 小怪数量)
TRASH_COUNT_BANDS = (
    (1, 8),
    (3, 10),
    (6, 14),
    (11, 20),
    (16, 28),
    (21, 36),
)

# 过渡保护：单次重算时，缩放属性最多只能变化到目标值的70%，
# 防止成员中途退出导致难度骤降/骤升引发灭团。
TRANSITION_FACTOR = 0.7


def clamp_int64(value):
    """把数值饱和到64位有符号整数范围。"""
    if value > INT64_MAX:
        return INT64_MAX
    if value < INT64_MIN:
        return INT64_MIN
    return value


def _difficulty_config(difficulty):
    if isinstance(difficulty, str):
        key = difficulty.lower()
    else:
        key = "normal"
    return DIFFICULTY_CONFIG.get(key, DIFFICULTY_CONFIG["normal"])

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
    current_hp: int = 0


@dataclass
class ScaleSnapshot:
    """某次缩放结果的快照，用于成员变化时的过渡保护。"""
    party_signature: tuple
    hp: int
    damage: int
    trash_count: int
    mechanics: list
    affix_level: int
    difficulty: str


def _lerp_limited(old, target, factor):
    """从old向target最多移动factor比例，保证难度平滑过渡。"""
    return old + (target - old) * factor

class DungeonScaling:
    def __init__(self):
        self.players: list[Player] = []
        self.base_trash_count = 10
        self.snapshots: dict[str, ScaleSnapshot] = {}
        self.transition_events: list[dict] = []

    def _party_size(self) -> int:
        return max(1, len(self.players))

    def average_item_level(self) -> float:
        """队伍平均装等；没人时以基准装等100计。"""
        if not self.players:
            return float(BASE_ITEM_LEVEL)
        return sum(p.item_level for p in self.players) / len(self.players)

    def _item_level_factor(self) -> float:
        # 装等每偏离基准100点缩放10%，并夹取在0.5~3.0，避免极端配装
        factor = self.average_item_level() / BASE_ITEM_LEVEL
        return min(3.0, max(0.5, factor))

    def _party_signature(self) -> tuple:
        return tuple(sorted((p.pid, int(p.item_level)) for p in self.players))

    def scale_boss_hp(self, boss: Boss) -> float:
        count = self._party_size()
        hp = boss.base_hp * count * self._item_level_factor()
        return float(clamp_int64(int(hp)))

    def scale_boss_damage(self, boss: Boss) -> float:
        count = self._party_size()
        damage = boss.base_damage * math.sqrt(count)
        return float(clamp_int64(int(damage)))

    def scale_trash_count(self) -> int:
        count = len(self.players)
        band_count = self.base_trash_count
        for threshold, trash in TRASH_COUNT_BANDS:
            if count >= threshold:
                band_count = trash
        return int(clamp_int64(band_count))

    def scale_mechanics(self, boss: Boss, difficulty: str) -> list:
        config = _difficulty_config(difficulty)
        mechanics = list(boss.mechanics)
        for mechanic in config["mechanics"]:
            if mechanic not in mechanics:
                mechanics.append(mechanic)
        return mechanics

    def scale_mechanic_params(self, base_params: dict, difficulty: str) -> dict:
        """难度提升时同步放大技能参数（伤害、持续时间等）。"""
        config = _difficulty_config(difficulty)
        power = config["mechanic_power"]
        scaled = {}
        for name, value in base_params.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                scaled[name] = type(value)(value * power)
            else:
                scaled[name] = value
        return scaled

    def scale_affix(self, boss: Boss, difficulty: str) -> int:
        config = _difficulty_config(difficulty)
        return int(clamp_int64(boss.affix_level + config["affix_bonus"]))

    def snapshot_boss(self, boss: Boss, difficulty: str = "normal") -> ScaleSnapshot:
        """记录当前队伍下BOSS的完整缩放快照（开战/重算时调用）。"""
        snapshot = ScaleSnapshot(
            party_signature=self._party_signature(),
            hp=int(self.scale_boss_hp(boss)),
            damage=int(self.scale_boss_damage(boss)),
            trash_count=self.scale_trash_count(),
            mechanics=self.scale_mechanics(boss, difficulty),
            affix_level=self.scale_affix(boss, difficulty),
            difficulty=difficulty,
        )
        self.snapshots[boss.bid] = snapshot
        if boss.current_hp <= 0:
            boss.current_hp = snapshot.hp
        return snapshot

    def recalc_on_player_leave(self, boss: Boss):
        self.recalculate(boss)

    def recalculate(self, boss: Boss, difficulty: str = None) -> ScaleSnapshot:
        """成员变化后重算难度，并对已开战的BOSS施加过渡保护。"""
        previous = self.snapshots.get(boss.bid)
        signature = self._party_signature()
        if previous is not None and previous.party_signature == signature:
            return previous

        current_difficulty = previous.difficulty if previous is not None else (
            difficulty if difficulty is not None else "normal"
        )
        if difficulty is not None:
            current_difficulty = difficulty

        target_hp = int(self.scale_boss_hp(boss))
        target_damage = int(self.scale_boss_damage(boss))
        target_trash = self.scale_trash_count()
        target_mechanics = self.scale_mechanics(boss, current_difficulty)
        target_affix = self.scale_affix(boss, current_difficulty)

        applied_hp = target_hp
        applied_damage = target_damage
        applied_trash = target_trash
        if previous is not None:
            # 过渡保护：血量与伤害单次只向目标移动70%，避免突变灭团
            applied_hp = int(clamp_int64(
                _lerp_limited(previous.hp, target_hp, TRANSITION_FACTOR)
            ))
            applied_damage = int(clamp_int64(
                _lerp_limited(previous.damage, target_damage, TRANSITION_FACTOR)
            ))
            applied_trash = max(
                previous.trash_count,
                int(_lerp_limited(previous.trash_count, target_trash, TRANSITION_FACTOR)),
            )
            self.transition_events.append({
                "bid": boss.bid,
                "old_party_size": len(previous.party_signature),
                "new_party_size": len(self.players),
                "target_hp": target_hp,
                "applied_hp": applied_hp,
            })

        snapshot = ScaleSnapshot(
            party_signature=signature,
            hp=applied_hp,
            damage=applied_damage,
            trash_count=applied_trash,
            mechanics=target_mechanics,
            affix_level=target_affix,
            difficulty=current_difficulty,
        )
        self.snapshots[boss.bid] = snapshot

        if previous is not None and boss.current_hp > 0 and previous.hp > 0:
            # 保持当前血量比例；若血量上限下调，当前血量不超过新上限
            ratio = boss.current_hp / previous.hp
            boss.current_hp = min(applied_hp, int(applied_hp * ratio))
        elif boss.current_hp <= 0:
            boss.current_hp = applied_hp
        return snapshot

    def scale_loot_ilevel(self, base_ilevel: int, difficulty: str) -> int:
        config = _difficulty_config(difficulty)
        return int(clamp_int64(int(round(base_ilevel * config["loot_multiplier"]))))

    def get_effective_hp(self, boss: Boss) -> int:
        hp = int(self.scale_boss_hp(boss))
        return max(0, int(clamp_int64(hp)))
