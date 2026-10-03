"""排课问题：基于 backtrack.py 的硬/软约束建模。

变量：每门课的每个课时（如 "数学#1"）。
取值：(天, 节次, 教室)。

硬约束（不满足则该课表无效）：
  H1 同一教师的两个课时不能在同一时段；
  H2 同一班级的两个课时不能在同一时段；
  H3 同一教室同一时段只能有一个课时；
  H4 同一门课的多个课时排在不同天；
  H5 教师不可用时段（用于制造约束冲突/强度扫描）。

软约束（代价，非负，越小越好；总代价 = 三者之和）：
  S1 下午惩罚：每节课排在第 3 节及以后罚 2（偏好上午）；
  S2 教室浪费：max(0, capacity - 班级人数)，鼓励容量匹配；
  S3 班级空堂：每个班级当天首末节课之间的空闲节数之和。

另有一条容量硬约束：
  H6 教室容量必须 >= 班级人数（坐不下即无效课表）。
"""

from backtrack import HardConstraint, SoftConstraint, Solver

DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri")


def _no_time_clash(values):
    """partial 谓词：已赋值课时的 (天, 节次) 两两不同。"""
    times = [(v[0], v[1]) for v in values]
    return len(set(times)) == len(times)


def _no_room_clash(values):
    """partial 谓词：已赋值课时的 (天, 节次, 教室) 两两不同。"""
    return len(set(values)) == len(values)


def _different_days(values):
    """partial 谓词：已赋值课时分布在不同天。"""
    return len({v[0] for v in values}) == len(values)


def _group_gap_cost(values):
    """某班级全部课时：按天统计首末节之间的空闲节数。"""
    by_day = {}
    for day, period, _room in values:
        by_day.setdefault(day, []).append(period)
    gaps = 0
    for periods in by_day.values():
        periods.sort()
        gaps += (periods[-1] - periods[0] + 1) - len(periods)
    return float(gaps)


def build_problem(courses, rooms, periods, teacher_unavailable=None):
    """构造排课 CSP。

    courses: [{"name", "teacher", "group", "sessions", "size"}]
    rooms:   {教室名: 容量}
    periods: 每天节次数（节次从 1 开始）
    teacher_unavailable: {教师: {(天, 节次), ...}}
    """
    teacher_unavailable = teacher_unavailable or {}
    sessions = []
    meta = {}
    domains = {}
    slots = [(d, p) for d in DAYS for p in range(1, periods + 1)]
    all_values = [(d, p, r) for d, p in slots for r in rooms]
    for course in courses:
        for i in range(1, course["sessions"] + 1):
            var = f"{course['name']}#{i}"
            sessions.append(var)
            meta[var] = course
            domains[var] = list(all_values)

    hard = []
    soft = []

    def related(key):
        groups = {}
        for var in sessions:
            groups.setdefault(meta[var][key], []).append(var)
        return [g for g in groups.values() if len(g) > 1]

    # H1 / H2 / H3：教师、班级、教室的冲突约束（partial 可前向检查）
    for group in related("teacher"):
        hard.append(HardConstraint(group, _no_time_clash,
                                   name=f"H1 教师不冲突:{group}", partial=True))
    for group in related("group"):
        hard.append(HardConstraint(group, _no_time_clash,
                                   name=f"H2 班级不冲突:{group}", partial=True))
    hard.append(HardConstraint(sessions, _no_room_clash,
                               name="H3 教室不冲突", partial=True))

    # H4：同一门课不同天
    for group in related("name"):
        hard.append(HardConstraint(group, _different_days,
                                   name=f"H4 不同天:{group}", partial=True))

    # H5：教师不可用时段（一元约束）
    for var in sessions:
        off = teacher_unavailable.get(meta[var]["teacher"])
        if off:
            hard.append(HardConstraint(
                (var,),
                lambda values, off=off: (values[0][0], values[0][1]) not in off,
                name=f"H5 教师不可用:{var}", partial=True))

    # H6：教室容量必须容纳班级人数（一元硬约束）
    for var in sessions:
        size = meta[var]["size"]
        hard.append(HardConstraint(
            (var,),
            lambda values, size=size: rooms[values[0][2]] >= size,
            name=f"H6 容量足够:{var}", partial=True))

    # S1：下午惩罚（一元，部分赋值即可计真实代价）
    for var in sessions:
        soft.append(SoftConstraint(
            (var,),
            lambda values: 2.0 if values[0][1] >= 3 else 0.0,
            name=f"S1 下午惩罚:{var}"))
    # S2：教室容量浪费（一元，非负）
    for var in sessions:
        size = meta[var]["size"]
        soft.append(SoftConstraint(
            (var,),
            lambda values, size=size: float(max(0, rooms[values[0][2]] - size)),
            name=f"S2 教室浪费:{var}"))
    # S3：班级空堂（n 元；部分赋值下界取 0，仍可采纳）
    for group in related("group"):
        soft.append(SoftConstraint(group, _group_gap_cost,
                                   name=f"S3 班级空堂:{group}"))

    return Solver(domains, hard, soft), sessions, meta


def courses_sample():
    """固定样例：3 门课 / 2 名教师 / 2 个班级，共 6 个课时。"""
    return [
        {"name": "数学", "teacher": "T1", "group": "G1", "sessions": 2, "size": 30},
        {"name": "英语", "teacher": "T2", "group": "G1", "sessions": 2, "size": 40},
        {"name": "物理", "teacher": "T2", "group": "G2", "sessions": 2, "size": 25},
    ]


ROOMS_SMALL = {"R1": 30, "R2": 50}
ROOMS_TIGHT = {"R1": 30}
