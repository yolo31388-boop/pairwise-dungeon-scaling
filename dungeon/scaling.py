"""副本动态难度与缩放系统 - 修复版

修复点:
1. BOSS血量同时考虑队伍人数与平均装等
2. BOSS伤害按人数平方根缩放, 而非线性
3. 小怪数量按人数区间配置
4. 难度切换同步升级技能机制(新增机制+参数放大)
5. 精英词缀强度随难度提升
6. 成员中途退出触发重算, 带过渡保护避免数值骤降
7. 掉落装等按难度系数映射
8. 所有对外数值经64位整数钳制, 防止溢出
"""
import math
from dataclasses import dataclass, field

INT64_MAX = 2 ** 63 - 1
INT64_MIN = -(2 ** 63)

# 成员变动后单次重算允许的最大下降比例(过渡保护)
MAX_TRANSITION_DROP = 0.20
# 重算后完全冻结的战斗节拍数, 之后逐拍向目标值收敛
TRANSITION_GRACE_TICKS = 3

# 难度系数配置
#   hp_mult:          难度血量系数
#   dmg_mult:         难度伤害系数
#   mechanic_scale:   机制数值参数的升级倍率
#   extra_mechanics:  该难度新增的技能机制(高难度继承低难度)
#   affix_bonus:      精英词缀强度增量
#   loot_bonus:       掉落装等增量
DIFFICULTY_CONFIG = {
    "normal": {
        "hp_mult": 1.0,
        "dmg_mult": 1.0,
        "mechanic_scale": 1.0,
        "extra_mechanics": (),
        "affix_bonus": 0,
        "loot_bonus": 0,
    },
    "heroic": {
        "hp_mult": 1.5,
        "dmg_mult": 1.25,
        "mechanic_scale": 1.25,
        "extra_mechanics": ("bloodlust",),
        "affix_bonus": 2,
        "loot_bonus": 15,
    },
    "mythic": {
        "hp_mult": 2.25,
        "dmg_mult": 1.5,
        "mechanic_scale": 1.5,
        "extra_mechanics": ("bloodlust", "devour"),
        "affix_bonus": 4,
        "loot_bonus": 30,
    },
}

DEFAULT_DIFFICULTY = "normal"
BASELINE_ITEM_LEVEL = 100

# 小怪数量的人数区间配置: (人数下限, 人数上限, 相对基础数量的倍率)
TRASH_COUNT_BRACKETS = (
    (1, 4, 0.6),
    (5, 9, 1.0),
    (10, 19, 1.4),
    (20, 40, 2.0),
)

# dict形式机制中需要随难度放大的数值参数
MECHANIC_SCALE_KEYS = frozenset({
    "damage", "healing", "amount", "value", "radius",
    "duration", "stacks", "count", "slow",
})


def clamp_int64(value):
    """将数值四舍五入并钳制到有符号64位整数区间。"""
    rounded = int(round(value))
    if rounded > INT64_MAX:
        return INT64_MAX
    if rounded < INT64_MIN:
        return INT64_MIN
    return rounded


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
    # 缩放器写入的运行时状态(最近一次缩放快照、过渡保护信息)
    scaling_state: dict = field(default_factory=dict)


class DungeonScaling:
    def __init__(self):
        self.players: list = []
        self.base_trash_count = 10

    # ------------------------------------------------------------------
    # 基础因子
    # ------------------------------------------------------------------
    def _difficulty_config(self, difficulty: str) -> dict:
        return DIFFICULTY_CONFIG.get(difficulty, DIFFICULTY_CONFIG[DEFAULT_DIFFICULTY])

    def _party_count(self) -> int:
        return len(self.players)

    def _average_item_level(self) -> float:
        if not self.players:
            return float(BASELINE_ITEM_LEVEL)
        return sum(p.item_level for p in self.players) / len(self.players)

    def _hp_factor(self, count: int, difficulty: str) -> float:
        """血量因子 = 人数 * 平均装等系数 * 难度系数。"""
        cfg = self._difficulty_config(difficulty)
        ilvl_coeff = self._average_item_level() / BASELINE_ITEM_LEVEL
        return count * ilvl_coeff * cfg["hp_mult"]

    def _damage_factor(self, count: int, difficulty: str) -> float:
        """伤害因子 = sqrt(人数) * 难度系数, 平方根缩放避免线性暴涨。"""
        cfg = self._difficulty_config(difficulty)
        return math.sqrt(max(count, 1)) * cfg["dmg_mult"]

    def _record_snapshot(self, boss: Boss, count: int, difficulty: str,
                         hp_factor: float, dmg_factor: float):
        boss.scaling_state.update({
            "count": count,
            "difficulty": difficulty,
            "hp_factor": hp_factor,
            "dmg_factor": dmg_factor,
            "raw_hp_factor": hp_factor,
            "raw_dmg_factor": dmg_factor,
            "protection_active": False,
            "grace_ticks": 0,
        })

    # ------------------------------------------------------------------
    # 1. BOSS血量(人数 + 平均装等)
    # ------------------------------------------------------------------
    def scale_boss_hp(self, boss: Boss, difficulty: str = DEFAULT_DIFFICULTY) -> float:
        count = self._party_count()
        state = boss.scaling_state
        if (state.get("protection_active")
                and state.get("count") == count
                and state.get("difficulty") == difficulty):
            hp_factor = state["hp_factor"]
        else:
            hp_factor = self._hp_factor(count, difficulty)
            dmg_factor = self._damage_factor(count, difficulty)
            self._record_snapshot(boss, count, difficulty, hp_factor, dmg_factor)
        return boss.base_hp * hp_factor

    # ------------------------------------------------------------------
    # 2. BOSS伤害(平方根缩放)
    # ------------------------------------------------------------------
    def scale_boss_damage(self, boss: Boss, difficulty: str = DEFAULT_DIFFICULTY) -> float:
        count = self._party_count()
        state = boss.scaling_state
        if (state.get("protection_active")
                and state.get("count") == count
                and state.get("difficulty") == difficulty):
            dmg_factor = state["dmg_factor"]
        else:
            hp_factor = self._hp_factor(count, difficulty)
            dmg_factor = self._damage_factor(count, difficulty)
            self._record_snapshot(boss, count, difficulty, hp_factor, dmg_factor)
        return boss.base_damage * dmg_factor

    # ------------------------------------------------------------------
    # 3. 小怪数量(按人数区间)
    # ------------------------------------------------------------------
    def scale_trash_count(self) -> int:
        count = self._party_count()
        if count <= 0:
            return 0
        multiplier = TRASH_COUNT_BRACKETS[-1][2]
        for low, high, mult in TRASH_COUNT_BRACKETS:
            if low <= count <= high:
                multiplier = mult
                break
        return clamp_int64(self.base_trash_count * multiplier)

    # ------------------------------------------------------------------
    # 4. 技能机制随难度升级(新增机制 + 参数放大)
    # ------------------------------------------------------------------
    def scale_mechanics(self, boss: Boss, difficulty: str) -> list:
        cfg = self._difficulty_config(difficulty)
        scale = cfg["mechanic_scale"]
        upgraded = []
        for mechanic in boss.mechanics:
            if isinstance(mechanic, dict):
                scaled = dict(mechanic)
                for key, value in scaled.items():
                    if key in MECHANIC_SCALE_KEYS and isinstance(value, (int, float)) \
                            and not isinstance(value, bool):
                        new_value = value * scale
                        scaled[key] = clamp_int64(new_value) if isinstance(value, int) \
                            else float(new_value)
                upgraded.append(scaled)
            else:
                upgraded.append(mechanic)
        upgraded.extend(cfg["extra_mechanics"])
        return upgraded

    # ------------------------------------------------------------------
    # 5. 精英词缀强度随难度提升
    # ------------------------------------------------------------------
    def scale_affix(self, boss: Boss, difficulty: str) -> int:
        cfg = self._difficulty_config(difficulty)
        return clamp_int64(boss.affix_level + cfg["affix_bonus"])

    # ------------------------------------------------------------------
    # 6. 成员退出重算 + 过渡保护
    # ------------------------------------------------------------------
    def recalc_on_player_leave(self, boss: Boss):
        """成员退出后重算缩放, 数值单次最多下降MAX_TRANSITION_DROP。"""
        state = boss.scaling_state
        count = self._party_count()
        difficulty = state.get("difficulty", DEFAULT_DIFFICULTY)

        if "hp_factor" not in state:
            self._record_snapshot(
                boss, count, difficulty,
                self._hp_factor(count, difficulty),
                self._damage_factor(count, difficulty),
            )
            return

        raw_hp = self._hp_factor(count, difficulty)
        raw_dmg = self._damage_factor(count, difficulty)
        # 过渡保护: 新值不得低于旧值的(1-最大降幅), 防止人数骤减导致灭团
        floor_hp = state["hp_factor"] * (1.0 - MAX_TRANSITION_DROP)
        floor_dmg = state["dmg_factor"] * (1.0 - MAX_TRANSITION_DROP)
        protected_hp = max(raw_hp, floor_hp)
        protected_dmg = max(raw_dmg, floor_dmg)

        state.update({
            "count": count,
            "difficulty": difficulty,
            "hp_factor": protected_hp,
            "dmg_factor": protected_dmg,
            "raw_hp_factor": raw_hp,
            "raw_dmg_factor": raw_dmg,
            "protection_active": True,
            "grace_ticks": TRANSITION_GRACE_TICKS,
        })

    def advance_transition(self, boss: Boss) -> bool:
        """推进一个战斗节拍, 冻结期过后逐步收敛到目标缩放值。

        返回过渡保护是否仍然生效。
        """
        state = boss.scaling_state
        if not state.get("protection_active"):
            return False
        if state.get("grace_ticks", 0) > 0:
            state["grace_ticks"] -= 1
            return True

        settled = True
        for factor_key, raw_key in (("hp_factor", "raw_hp_factor"),
                                    ("dmg_factor", "raw_dmg_factor")):
            current = state[factor_key]
            target = state[raw_key]
            if current > target:
                current = max(target, current * (1.0 - MAX_TRANSITION_DROP))
                state[factor_key] = current
            if current > target:
                settled = False
        if settled:
            state["protection_active"] = False
        return state["protection_active"]

    # ------------------------------------------------------------------
    # 7. 掉落装等按难度映射
    # ------------------------------------------------------------------
    def scale_loot_ilevel(self, base_ilevel: int, difficulty: str) -> int:
        cfg = self._difficulty_config(difficulty)
        return clamp_int64(base_ilevel + cfg["loot_bonus"])

    # ------------------------------------------------------------------
    # 8. 64位整数钳制的有效血量
    # ------------------------------------------------------------------
    def get_effective_hp(self, boss: Boss, difficulty: str = DEFAULT_DIFFICULTY) -> int:
        return clamp_int64(self.scale_boss_hp(boss, difficulty))
