"""任务管线（重构前版本）。

本模块里所有"必须如何"的约定都只写在注释中，靠口口相传维持：

  - Job 必须是 dict，且必须包含 id / kind / payload / priority 四个键；
    id 是非空 str，kind 只能是 email/report/cleanup，payload 必须是 dict。
  - priority 必须是 0~9 的整数（0 最紧急）。
  - 构造时 max_retries 只能传 0~5 的整数，timeout 必须在 (0, 300] 秒之间。
  - 调用顺序固定：open() 只能调用一次，之后才能 submit()，run() 之前
    至少要 submit() 一个 job，最后必须恰好 close() 一次；close 之后
    不能再 submit/run。
  - 同一个 Pipeline 会话内 job id 不能重复。
  - Pipeline 对象只能在调用 open() 的那个线程里使用（线程亲和）。
  - run() 不允许并发/重入（回调里不能再调 run）。
  - run() 返回的每个 result 必须是 dict，键恰好是 job_id/status/attempts，
    status 取值 ok|failed，attempts 是 >=1 的整数。

以上约定没有任何强制手段，新人改坏后测试也抓不到。
"""

VALID_KINDS = {"email", "report", "cleanup"}


class Pipeline:
    def __init__(self, max_retries=3, timeout=30.0):
        self.max_retries = max_retries
        self.timeout = timeout
        self.state = "new"
        self.owner_thread = None
        self.jobs = []
        self.seen_ids = set()
        self.running = False

    def open(self):
        # 只能在 new 状态调用一次
        self.state = "open"
        import threading
        self.owner_thread = threading.get_ident()

    def submit(self, job):
        # 调用方要保证形状正确、id 不重复、priority 合法
        self.seen_ids.add(job["id"])
        self.jobs.append(job)

    def run(self, on_job=None):
        # 前提：已经 open、jobs 非空、没有别的 run 在跑
        results = []
        for job in self.jobs:
            if on_job is not None:
                on_job(job)
            results.append({
                "job_id": job["id"],
                "status": "ok",
                "attempts": 1 + self.max_retries,
            })
        return results

    def close(self):
        # 只能在 open 后调用一次
        self.state = "closed"
        self.jobs = []
