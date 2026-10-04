"""任务管线（重构后版本）：隐式约定全部落成可执行断言。

每条约定有编号 C1..C9（分类与断言对应关系见 docs/CONVENTIONS.md），
违反时抛出 ContractViolation，消息前缀即约定编号，便于定位。
"""

from __future__ import annotations

import threading


class ContractViolation(Exception):
    """违反可执行约定（C1..C9）时抛出。"""


VALID_KINDS = frozenset({"email", "report", "cleanup"})

PRIORITY_MIN = 0
PRIORITY_MAX = 9
RETRIES_MIN = 0
RETRIES_MAX = 5
TIMEOUT_MIN = 0.0   # 开区间下界
TIMEOUT_MAX = 300.0  # 闭区间上界

_RESULT_KEYS = frozenset({"job_id", "status", "attempts"})
_RESULT_STATUSES = frozenset({"ok", "failed"})


def _require(condition, code, message):
    if not condition:
        raise ContractViolation("[{0}] {1}".format(code, message))


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def validate_job(job):
    """C1（形状）+ C5（priority 取值范围）。"""
    _require(isinstance(job, dict), "C1", "job must be a dict")
    missing = {"id", "kind", "payload", "priority"} - set(job)
    _require(not missing, "C1", "job missing keys: {0}".format(sorted(missing)))
    _require(isinstance(job["id"], str) and job["id"] != "",
             "C1", "job['id'] must be a non-empty str")
    _require(job["kind"] in VALID_KINDS,
             "C1", "job['kind'] must be one of {0}".format(sorted(VALID_KINDS)))
    _require(isinstance(job["payload"], dict),
             "C1", "job['payload'] must be a dict")
    _require(_is_int(job["priority"]),
             "C5", "job['priority'] must be an int (bool not allowed)")
    _require(PRIORITY_MIN <= job["priority"] <= PRIORITY_MAX,
             "C5", "job['priority'] must be in [{0}, {1}]".format(PRIORITY_MIN, PRIORITY_MAX))


def validate_result(result):
    """C2（run() 返回值的形状后置条件）。"""
    _require(isinstance(result, dict), "C2", "result must be a dict")
    _require(set(result) == _RESULT_KEYS,
             "C2", "result keys must be exactly {0}".format(sorted(_RESULT_KEYS)))
    _require(isinstance(result["job_id"], str) and result["job_id"] != "",
             "C2", "result['job_id'] must be a non-empty str")
    _require(result["status"] in _RESULT_STATUSES,
             "C2", "result['status'] must be one of {0}".format(sorted(_RESULT_STATUSES)))
    _require(_is_int(result["attempts"]) and result["attempts"] >= 1,
             "C2", "result['attempts'] must be an int >= 1")


class Pipeline:
    """一次性生命周期：new -> open -> closed，不可重开。"""

    def __init__(self, max_retries=3, timeout=30.0):
        # C6：构造参数取值范围
        _require(_is_int(max_retries),
                 "C6", "max_retries must be an int (bool not allowed)")
        _require(RETRIES_MIN <= max_retries <= RETRIES_MAX,
                 "C6", "max_retries must be in [{0}, {1}]".format(RETRIES_MIN, RETRIES_MAX))
        _require(isinstance(timeout, (int, float)) and not isinstance(timeout, bool),
                 "C6", "timeout must be a number")
        _require(TIMEOUT_MIN < timeout <= TIMEOUT_MAX,
                 "C6", "timeout must be in ({0}, {1}] seconds".format(TIMEOUT_MIN, TIMEOUT_MAX))
        self.max_retries = max_retries
        self.timeout = float(timeout)
        self._state = "new"
        self._owner_thread = None
        self._jobs = []
        self._seen_ids = set()
        self._running = False

    def _check_thread_affinity(self):
        # C8：线程亲和——只能在调用 open() 的线程里使用
        _require(threading.get_ident() == self._owner_thread,
                 "C8", "Pipeline is confined to the thread that called open()")

    def _check_open(self, method):
        # C3：调用顺序——open() 之后、close() 之前
        _require(self._state == "open",
                 "C3", "{0}() requires state 'open' (current: {1!r})".format(method, self._state))

    def open(self):
        # C3：open() 恰好一次，且必须是第一个生命周期调用
        _require(self._state == "new",
                 "C3", "open() must be called exactly once on a fresh Pipeline")
        self._state = "open"
        self._owner_thread = threading.get_ident()

    def submit(self, job):
        self._check_open("submit")
        self._check_thread_affinity()
        validate_job(job)  # C1 + C5
        # C7：同一会话内 job id 唯一
        _require(job["id"] not in self._seen_ids,
                 "C7", "duplicate job id in this session: {0!r}".format(job["id"]))
        self._seen_ids.add(job["id"])
        self._jobs.append(dict(job))

    def run(self, on_job=None):
        self._check_open("run")
        self._check_thread_affinity()
        # C4：run() 之前至少提交一个 job
        _require(len(self._jobs) > 0,
                 "C4", "run() requires at least one submitted job")
        # C9：run() 不允许并发/重入
        _require(not self._running,
                 "C9", "run() must not be called concurrently or re-entrantly")
        self._running = True
        try:
            results = []
            for job in self._jobs:
                if on_job is not None:
                    on_job(job)
                results.append({
                    "job_id": job["id"],
                    "status": "ok",
                    "attempts": 1 + self.max_retries,
                })
        finally:
            self._running = False
        for result in results:
            validate_result(result)  # C2：后置条件
        return results

    def close(self):
        self._check_open("close")
        self._check_thread_affinity()
        self._state = "closed"
        self._jobs = []
