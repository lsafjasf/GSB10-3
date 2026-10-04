"""四个测试场景：无竞争、单次竞争（丢失更新）、死锁、特定交错才暴露。

每个场景返回 (program, check, description)：
* program(sched) 在 sched 上 spawn 线程；
* check(sched) 在所有线程结束后对最终状态断言；
* None 表示该场景的缺陷是死锁本身，无需额外断言。
"""


def no_race():
    description = "无竞争：两个线程各写各的变量"

    def program(s):
        a = s.var("a", 0)
        b = s.var("b", 0)

        def ta():
            a.write(1)
            a.write(2)

        def tb():
            b.write(1)
            b.write(2)

        s.spawn(ta, "TA")
        s.spawn(tb, "TB")

    def check(s):
        assert s.peek("a") == 2 and s.peek("b") == 2

    return program, check, description


def single_race():
    description = "单次竞争：无锁 read-modify-write，交错时丢失更新"

    def program(s):
        counter = s.var("counter", 0)

        def inc():
            tmp = counter.read()
            counter.write(tmp + 1)

        s.spawn(inc, "TA")
        s.spawn(inc, "TB")

    def check(s):
        value = s.peek("counter")
        assert value == 2, f"lost update: counter={value}, expected 2"

    return program, check, description


def deadlock():
    description = "死锁：两个线程以相反顺序获取两把锁"

    def program(s):
        m1 = s.mutex("m1")
        m2 = s.mutex("m2")

        def ta():
            m1.acquire()
            m2.acquire()
            m2.release()
            m1.release()

        def tb():
            m2.acquire()
            m1.acquire()
            m1.release()
            m2.release()

        s.spawn(ta, "TA")
        s.spawn(tb, "TB")

    return program, None, description


def specific_interleaving():
    description = "特定交错：两个生产者写 (data, flag)，观察者两次读必须恰好跨过两次写才会撕裂"
    observed = {}

    def program(s):
        data = s.var("data", 0)
        flag = s.var("flag", 0)

        def pa():                       # 生产者 A
            data.write(1)
            flag.write(1)

        def pb():                       # 生产者 B
            data.write(2)
            flag.write(2)

        def pc():                       # 观察者：先读 flag 再读 data
            f = flag.read()
            d = data.read()
            observed["pair"] = (f, d)

        s.spawn(pa, "PA")
        s.spawn(pb, "PB")
        s.spawn(pc, "PC")

    def check(s):
        f, d = observed["pair"]
        if f == 0:
            return  # flag=0 表示尚未有生产者发布，此时 data 为任何值都合法
        assert f == d, (
            f"torn read: flag={f}, data={d}（flag 已发布为 {f}，data 却属于另一次写入）"
        )

    return program, check, description


ALL = [no_race, single_race, deadlock, specific_interleaving]
