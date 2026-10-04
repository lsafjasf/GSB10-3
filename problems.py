"""排课与配置两类示例问题的显式建模。"""
from __future__ import annotations

from typing import Dict, List, Tuple

from cpsolver import Problem

Placement = Tuple[int, int]  # (时段, 教室)


# ================= 排课 =================

class SchedulingSpec:
    def __init__(self, n_slots: int, rooms: Dict[str, int],
                 teachers: Dict[str, str],
                 unavailable: Dict[str, List[int]] = None,
                 locked: Dict[str, Placement] = None):
        self.n_slots = n_slots
        self.rooms = rooms                      # 教室名 -> 容量
        self.teachers = teachers                # 课程 -> 教师
        self.unavailable = unavailable or {}    # 课程 -> 不可排时段
        self.locked = locked or {}              # 课程 -> 固定 (时段, 教室)

    @property
    def courses(self) -> List[str]:
        return list(self.teachers)


def build_scheduling(spec: SchedulingSpec, name: str = "scheduling") -> Problem:
    """硬约束：
      H1 同一教师不能同时段上两门课；
      H2 同一教室不能同时段排两门课；
      H3 教室容量 >= 选课人数（course_size 在 spec 中未建模，这里按固定阈值）；
      H4 课程不能排入 unavailable 的时段；
      H5 locked 课程必须排到固定位置。
    软约束（代价均非负）：
      S1 晚时段代价：课程排在最后一个时段 +1（师生偏好早时段）；
      S2 教师课表紧凑度：同一教师两课之间跨时段，每跨 1 个时段 +1；
      S3 教室浪费：容量超出基准的余量每满 20 人 +1。
    S2 提供部分赋值下界：已排课之间的跨度代价（完整代价的组成部分，不高估）。
    """
    p = Problem(name)
    rooms = list(spec.rooms)
    course_size = 40

    for course in spec.courses:
        if course in spec.locked:
            domain = [spec.locked[course]]
        else:
            domain = [(s, r)
                      for s in range(spec.n_slots)
                      for r in rooms
                      if s not in spec.unavailable.get(course, [])
                      and spec.rooms[r] >= course_size]
        p.add_variable(course, domain)

    def teacher_ok(values):
        seen = {}
        for c, (slot, _room) in values.items():
            t = spec.teachers[c]
            if (t, slot) in seen:
                return False
            seen[(t, slot)] = c
        return True

    def room_ok(values):
        used = set()
        for _c, (slot, room) in values.items():
            if (slot, room) in used:
                return False
            used.add((slot, room))
        return True

    p.add_hard(spec.courses, teacher_ok, name="H1 教师不冲突")
    p.add_hard(spec.courses, room_ok, name="H2 教室不冲突")

    p.add_soft(
        spec.courses,
        cost=lambda values: sum(
            1 for (_s, room) in values.values()
            for slot in [_s]
            if slot == spec.n_slots - 1),
        name="S1 晚时段代价")

    def compact_cost(values):
        by_teacher: Dict[str, List[int]] = {}
        for c, (slot, _r) in values.items():
            by_teacher.setdefault(spec.teachers[c], []).append(slot)
        total = 0.0
        for slots in by_teacher.values():
            slots = sorted(slots)
            total += sum(b - a for a, b in zip(slots, slots[1:]))
        return total

    def compact_lower(values):
        # 部分赋值下已能确定的跨度，未来加课只会使跨度和不减反增
        by_teacher: Dict[str, List[int]] = {}
        for c, (slot, _r) in values.items():
            by_teacher.setdefault(spec.teachers[c], []).append(slot)
        total = 0.0
        for slots in by_teacher.values():
            slots = sorted(slots)
            total += sum(b - a for a, b in zip(slots, slots[1:]))
        return total

    p.add_soft(spec.courses, compact_cost, compact_lower, name="S2 课表紧凑")

    p.add_soft(
        spec.courses,
        cost=lambda values: sum(
            (spec.rooms[room] - course_size) // 20
            for _c, (_slot, room) in values.items()),
        name="S3 教室余量浪费")
    return p


# ================= 配置（服务部署到服务器） =================

class ConfigSpec:
    def __init__(self, machines: Dict[str, Tuple[int, int]],
                 services: List[str],
                 demands: Dict[str, int],
                 anti_affinity: List[Tuple[str, str]],
                 locked: Dict[str, str] = None):
        self.machines = machines            # 机器名 -> (CPU, 槽位上限)
        self.services = services
        self.demands = demands              # 服务 -> CPU 需求
        self.anti_affinity = anti_affinity  # 不能同机的服务对
        self.locked = locked or {}


def build_config(spec: ConfigSpec, name: str = "config") -> Problem:
    """硬约束：
      H1 每台机器上服务 CPU 需求之和 <= 机器 CPU；
      H2 每台机器上服务数量 <= 槽位上限；
      H3 anti-affinity 服务对不能同机；
      H4 locked 服务必须在指定机器上。
    软约束：
      S1 负载均衡：各机器利用率（需求/容量）的极差 *10 取整代价；
         部分赋值下界：仅对已有服务的机器计算极差，未分配机器未来只会
         增加利用率，不会缩小当前已观察到的极差（保守取下界 0 的安全子集：
         这里直接用 0 作为部分下界，保证 admissible）。
    """
    p = Problem(name)
    machines = list(spec.machines)
    for svc in spec.services:
        domain = [spec.locked[svc]] if svc in spec.locked else machines
        p.add_variable(svc, domain)

    def capacity_ok(values):
        load = {m: 0 for m in machines}
        for svc, m in values.items():
            load[m] += spec.demands[svc]
        return all(load[m] <= spec.machines[m][0] for m in machines)

    def slot_ok(values):
        cnt = {m: 0 for m in machines}
        for _svc, m in values.items():
            cnt[m] += 1
        return all(cnt[m] <= spec.machines[m][1] for m in machines)

    def anti_ok(values):
        for a, b in spec.anti_affinity:
            if a in values and b in values and values[a] == values[b]:
                return False
        return True

    p.add_hard(spec.services, capacity_ok, name="H1 CPU 容量")
    p.add_hard(spec.services, slot_ok, name="H2 槽位上限")
    for i, pair in enumerate(spec.anti_affinity):
        p.add_hard(pair, anti_ok, name=f"H3 反亲和{i}")

    def balance_cost(values):
        load = {m: 0 for m in machines}
        for svc, m in values.items():
            load[m] += spec.demands[svc]
        utils = [load[m] / spec.machines[m][0] for m in machines]
        return float(int((max(utils) - min(utils)) * 10 + 1e-9))

    p.add_soft(spec.services, balance_cost, name="S1 负载均衡极差")
    return p
