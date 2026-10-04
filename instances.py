"""不同约束强度的基准实例工厂。"""
from problems import SchedulingSpec, ConfigSpec, build_scheduling, build_config


def scheduling_instances():
    rooms3 = {"R1": 40, "R2": 60, "R3": 80}
    rooms2 = {"R1": 40, "R2": 60}
    weak = SchedulingSpec(
        n_slots=4, rooms=rooms2,
        teachers={"C1": "T1", "C2": "T2", "C3": "T3",
                  "C4": "T4", "C5": "T5"})
    medium = SchedulingSpec(
        n_slots=4, rooms=rooms2,
        teachers={"C1": "T1", "C2": "T1", "C3": "T2",
                  "C4": "T2", "C5": "T3", "C6": "T3"})
    strong = SchedulingSpec(
        n_slots=3, rooms=rooms2,
        teachers={"C1": "T1", "C2": "T1", "C3": "T2",
                  "C4": "T2", "C5": "T3", "C6": "T3"},
        unavailable={"C1": [0], "C3": [0, 2], "C5": [2]})
    return [
        ("排课-弱约束", build_scheduling(weak, "sched_weak")),
        ("排课-中约束", build_scheduling(medium, "sched_medium")),
        ("排课-强约束", build_scheduling(strong, "sched_strong")),
    ]


def config_instances():
    weak = ConfigSpec(
        machines={"M1": (8, 4), "M2": (8, 4), "M3": (8, 4)},
        services=["a", "b", "c", "d", "e"],
        demands={"a": 2, "b": 2, "c": 2, "d": 2, "e": 2},
        anti_affinity=[("a", "b")])
    medium = ConfigSpec(
        machines={"M1": (6, 3), "M2": (6, 3)},
        services=["a", "b", "c", "d", "e"],
        demands={"a": 2, "b": 2, "c": 2, "d": 3, "e": 1},
        anti_affinity=[("a", "b"), ("c", "d")])
    strong = ConfigSpec(
        machines={"M1": (7, 3), "M2": (7, 3)},
        services=["a", "b", "c", "d", "e"],
        demands={"a": 3, "b": 3, "c": 2, "d": 2, "e": 2},
        anti_affinity=[("a", "b"), ("c", "d"), ("a", "c"), ("b", "d")])
    return [
        ("配置-弱约束", build_config(weak, "cfg_weak")),
        ("配置-中约束", build_config(medium, "cfg_medium")),
        ("配置-强约束", build_config(strong, "cfg_strong")),
    ]


def all_instances():
    return scheduling_instances() + config_instances()
