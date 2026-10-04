"""物流文本实体抽取（规则 + 位置线索，标准库 only）。

规则分组：
  A 归一化：NFKC、全角标点、繁体最小映射、跨行拼接（保留换行证据）
  B 电话：手机/座机/400 三模式 + 边界校验，全量收集（支持一行多个）
  C 姓名：标签 > 括号 > 位置线索（距电话最近的 2-4 字纯中文候选）
  D 地址：省->市->区 贪心锚定；缺省时城市表反推省份；仅区县时全局搜索兜底；
         详细地址 = 行政区之后、姓名/电话/停止标签之前
  E 置信度：按命中层级累加

每条规则命中记录到 result.rules，explain() 可打印命中说明。
"""
import re
import unicodedata
from dataclasses import dataclass, field

from .regions import PROVINCES, CITY_BY_PROVINCE, PROVINCE_OF_CITY, MUNICIPALITIES, T2S

MOBILE_RE = re.compile(r"(?<!\d)(?:\+?86[\s-]?)?1[3-9]\d(?:[\s-]?\d{4}){2}(?!\d)")
LANDLINE_RE = re.compile(r"(?<!\d)0\d{2,3}[\s-]?\d{7,8}(?!\d)")
P400_RE = re.compile(r"(?<!\d)400[\s-]?\d{3}[\s-]?\d{4}(?!\d)")

NAME_LABEL_RE = re.compile(
    r"(收件人|收货人|联系人|寄件人|姓名|客户|买家|掌柜)\s*[:：]?\s*"
    r"([一-龥·]{2,15}(?:[一-龥A-Za-z]{0,4})?)"
)
ADDR_LABEL_RE = re.compile(r"(地址|收货地址|收件地址|联系地址|住址|详细地址|地址信息)\s*[:：]?\s*")
PAREN_NAME_RE = re.compile(r"[（(]\s*([一-龥]{2,4}(?:[·•][一-龥]{1,4})?)\s*[)）]")

NAME_SUFFIXES = ("先生", "女士", "小姐", "老师", "经理")

NAME_FORBID = (
    "省", "市", "区", "县", "旗", "镇", "乡", "村", "路", "街", "道", "巷", "弄",
    "号", "栋", "幢", "座", "层", "室", "楼", "单元", "园", "苑", "城", "中心",
    "广场", "大厦", "大学", "学院", "学部", "医院", "公司", "集团", "科技", "有限",
    "工业园", "开发区", "地址", "收件", "收货", "联系", "电话", "手机", "姓名",
    "客户", "买家", "掌柜", "备注", "备用", "客服", "先生", "女士", "小姐",
    "老师", "经理", "自治", "特别行政",
)

DETAIL_STOP_LABELS = ("收件人", "收货人", "联系人", "寄件人", "姓名", "客户", "买家",
                      "掌柜", "电话", "手机", "联系方式", "备注", "邮编")

DETAIL_STRIP = " \t,，.。;；:：、|/\\-—_~`'\""

_WS = " \t"


@dataclass
class Span:
    start: int
    end: int
    text: str


@dataclass
class Result:
    name: str = ""
    name_conf: float = 0.0
    phones: list = field(default_factory=list)      # [(digits, conf)]
    province: str = ""
    city: str = ""
    district: str = ""
    detail: str = ""
    address_conf: float = 0.0
    rules: list = field(default_factory=list)

    def hit(self, rule, detail):
        self.rules.append("%s | %s" % (rule, detail))


# ---------------------------------------------------------------- A 归一化

def normalize(text):
    t = unicodedata.normalize("NFKC", text)
    t = t.replace("：", ":").replace("，", ",").replace("；", ";")
    t = "".join(T2S.get(ch, ch) for ch in t)
    lines = [ln.strip() for ln in t.splitlines()]
    joined = " ".join(ln for ln in lines if ln)
    return joined, [ln for ln in lines if ln]


def _mask(text, spans, ch=""):
    buf = list(text)
    for s, e in spans:
        for i in range(s, min(e, len(buf))):
            buf[i] = ch
    return "".join(buf)


def _skip_ws(text, i):
    while i < len(text) and text[i] in _WS:
        i += 1
    return i


def _strip_suffix(val):
    for suf in NAME_SUFFIXES:
        if val.endswith(suf) and len(val) - len(suf) >= 2:
            return val[:-len(suf)]
    return val


# ---------------------------------------------------------------- B 电话

def extract_phones(flat, res):
    spans = []
    for m in MOBILE_RE.finditer(flat):
        spans.append((m.start(), m.end(), m.group(), "B1 手机"))
    for m in LANDLINE_RE.finditer(flat):
        spans.append((m.start(), m.end(), m.group(), "B2 座机"))
    for m in P400_RE.finditer(flat):
        spans.append((m.start(), m.end(), m.group(), "B3 400"))
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    kept = []
    for s, e, raw, rule in spans:
        if any(s < ke and e > ks for ks, ke, _, _ in kept):
            continue
        digits = re.sub(r"\D", "", raw)
        if digits.startswith("86") and len(digits) == 13:
            digits = digits[2:]
        kept.append((s, e, raw, rule))
        conf = 0.99 if rule.startswith("B1") else 0.95
        res.phones.append((digits, conf))
        res.hit(rule, "命中 %r -> %s" % (raw, digits))
    return kept


# ---------------------------------------------------------------- C 姓名

def extract_name(flat, phone_spans, res):
    # C1 标签
    best = None
    for m in NAME_LABEL_RE.finditer(flat):
        label, val = m.group(1), m.group(2).strip()
        if any(k in val for k in ("地址", "电话", "手机")):
            continue
        val = re.split(r"[,，。;；\s]", val)[0]
        val = _strip_suffix(val)
        if len(val) < 2:
            continue
        pri = 0 if label in ("收件人", "收货人", "联系人", "姓名") else 1
        if best is None or pri < best[0]:
            best = (pri, val, m.start(2), m.start(2) + len(val))
    if best:
        _, val, s, e = best
        res.hit("C1 姓名标签", "标签后取值 %r" % val)
        return val, (s, e)

    # C2 括号：括号内容需贴近电话（<=8 字符），否则视为地址注释如"（近地铁口）"
    for m in PAREN_NAME_RE.finditer(flat):
        val = m.group(1)
        if any(k in val for k in NAME_FORBID):
            continue
        if phone_spans:
            dist = min(abs(m.start(1) - pe) if m.start(1) >= pe else abs(ps - m.end(1))
                       for ps, pe, _, _ in phone_spans)
            if dist > 8:
                continue
        res.hit("C2 括号姓名", "括号内 %r（贴近电话）" % val)
        return val, (m.start(1), m.end(1))

    # C3 位置线索
    masked = _mask(flat, [(s, e) for s, e, _, _ in phone_spans])
    masked = NAME_LABEL_RE.sub(lambda m: "" * (m.end() - m.start()), masked)
    masked = ADDR_LABEL_RE.sub(lambda m: "" * (m.end() - m.start()), masked)
    masked = _mask(masked, _province_spans(masked))
    cands = []
    for m in re.finditer(r"[一-龥]{2,4}(?:[·•][一-龥]{1,4})?", masked):
        val = m.group()
        stripped = _strip_suffix(val)
        if stripped != val and len(stripped) >= 2:
            val = stripped
        if any(k in val for k in NAME_FORBID):
            continue
        if val in PROVINCES or val in PROVINCE_OF_CITY:
            continue
        cands.append((m.start(), m.start() + len(val), val))
    # C3b 地址尾粘连："...100号许三" -> 末尾 号/室/栋 后的 2-3 字
    m_end = re.search(r"[号室栋幢楼层院]([一-龥]{2,3})\s*$", masked)
    if m_end:
        val = m_end.group(1)
        if not any(k in val for k in NAME_FORBID):
            cands.append((m_end.start(1), m_end.end(1), val))
            res.hit("C3b 地址尾粘连", "末尾候选 %r" % val)
    if not cands:
        return "", None

    def score(c):
        s, e, _ = c
        if phone_spans:
            return min(abs(s - pe) if s >= pe else abs(ps - e)
                       for ps, pe, _, _ in phone_spans)
        return s
    cands.sort(key=lambda c: (score(c), c[0]))
    s, e, val = cands[0]
    res.hit("C3 位置姓名", "候选 %r（距电话最近/最靠前）" % val)
    return val, (s, e)


def _province_spans(text):
    spans = []
    aliases = sorted(PROVINCES, key=len, reverse=True)
    i = 0
    while i < len(text):
        hit = None
        for a in aliases:
            if text.startswith(a, i):
                hit = a
                break
        if hit:
            spans.append((i, i + len(hit)))
            i += len(hit)
        else:
            i += 1
    return spans


# ---------------------------------------------------------------- D 地址

def _search_alias(text, aliases, start):
    """在 text[start:] 中找最早出现的别名，返回 (pos, alias) 或 None。"""
    best = None
    for a in aliases:
        p = text.find(a, start)
        if p != -1 and (best is None or p < best[0] or (p == best[0] and len(a) > len(best[1]))):
            best = (p, a)
    return best


def parse_region(flat, masked, name_span, res):
    """返回 (province, city, district, region_end)。"""
    addr_start = 0
    m = ADDR_LABEL_RE.search(flat)
    if m:
        addr_start = m.end()
        res.hit("D0 地址标签", "标签 %r 之后作为地址搜索起点(%d)" % (m.group(1), addr_start))

    province, city, district = "", "", ""
    idx = None
    # D1 省：从 addr_start 起全局搜索最早锚点
    aliases = sorted(PROVINCES, key=len, reverse=True)
    hit = _search_alias(masked, aliases, addr_start)
    if hit:
        pos, alias = hit
        province = PROVINCES[alias]
        idx = pos + len(alias)
        res.hit("D1 省级匹配", "别名 %r@%d -> %s" % (alias, pos, province))
    # D2 市
    if province:
        idx = _skip_ws(flat, idx)
        if province in MUNICIPALITIES:
            city = province
            res.hit("D2 直辖市", "市级=省级 %s" % city)
        else:
            for c in CITY_BY_PROVINCE.get(province, []):
                if flat.startswith(c, idx):
                    city = c
                    idx += len(c)
                    res.hit("D2 城市表匹配", "%s" % c)
                    break
            if not city:
                m2 = re.match(r"[一-龥]{2,6}(?:市|州|盟|地区)", flat[idx:])
                if m2:
                    city = m2.group()
                    idx += len(city)
                    res.hit("D2 通用城市后缀", "%s" % city)
    else:
        # 无省：城市表反推，再通用城市后缀
        hit = _search_alias(masked, sorted(PROVINCE_OF_CITY, key=len, reverse=True), addr_start)
        if hit:
            pos, c = hit
            city, province = c, PROVINCE_OF_CITY[c]
            idx = pos + len(c)
            res.hit("D1' 城市反推省", "%s@%d -> %s" % (c, pos, province))
        else:
            m2 = re.search(r"[一-龥]{2,6}(?:市|州|盟)", masked[addr_start:])
            if m2:
                city = m2.group()
                idx = addr_start + m2.end()
                res.hit("D2 通用城市后缀(无省)", "%s" % city)
    # D3 区/县
    if idx is not None:
        idx = _skip_ws(flat, idx)
        window = flat[idx:idx + 20]
        m3 = re.match(r"[一-龥]{2,8}?(?:区|县|旗|市|區|縣)", window)
        if m3:
            district = m3.group()
            idx += len(district)
            res.hit("D3 区县后缀", "%s" % district)
    else:
        # 仅区县兜底：全局搜索"xx区/县"
        m3 = re.search(r"[一-龥]{2,8}?(?:区|县|旗)", masked[addr_start:])
        if m3:
            district = m3.group()
            idx = addr_start + m3.end()
            res.hit("D3' 仅区县兜底", "%s" % district)
    region_end = idx if idx is not None else addr_start
    return province, city, district, region_end


def extract_detail(flat, region_end, name_span, phone_spans, res):
    stops = [len(flat)]
    if name_span and name_span[0] >= region_end:
        stops.append(name_span[0])
    for s, e, _, _ in phone_spans:
        if s >= region_end:
            stops.append(s)
    for lab in DETAIL_STOP_LABELS:
        p = flat.find(lab, region_end)
        if p != -1:
            stops.append(p)
    end = min(stops)
    detail = flat[region_end:end].strip(DETAIL_STRIP)
    res.hit("D4 详细地址截断", "区间 [%d,%d) 清洗分隔符" % (region_end, end))
    return detail


# ---------------------------------------------------------------- 主入口

def extract(text):
    res = Result()
    flat, lines = normalize(text)
    if len(lines) > 1:
        res.hit("A3 跨行拼接", "%d 行 -> 1 行" % len(lines))
    res.hit("A1 归一化", "NFKC + 全角标点 + 繁体最小映射")

    phone_spans = extract_phones(flat, res)
    name, name_span = extract_name(flat, phone_spans, res)
    res.name = name
    if name:
        if any(r.startswith("C1") for r in res.rules):
            res.name_conf = 0.95
        elif any(r.startswith("C2") for r in res.rules):
            res.name_conf = 0.9
        else:
            res.name_conf = 0.8

    masked = _mask(flat, [(s, e) for s, e, _, _ in phone_spans])
    if name_span:
        masked = _mask(masked, [name_span])
    province, city, district, region_end = parse_region(flat, masked, name_span, res)
    res.province, res.city, res.district = province, city, district
    res.detail = extract_detail(flat, region_end, name_span, phone_spans, res)

    conf = (0.4 if province else 0.0) + (0.3 if city else 0.0) + \
           (0.2 if district else 0.0) + (0.1 if res.detail else 0.0)
    res.address_conf = round(min(conf, 1.0), 2)
    res.hit("E 置信度", "address=%.2f name=%.2f" % (res.address_conf, res.name_conf))
    return res


def explain(text):
    res = extract(text)
    out = ["== 输入 ==", text, "== 规则命中 =="]
    out += ["  " + r for r in res.rules]
    out.append("== 结果 ==")
    out.append("  name=%r phones=%r" % (res.name, [p for p, _ in res.phones]))
    out.append("  省=%r 市=%r 区=%r 详细=%r (addr_conf=%.2f)"
               % (res.province, res.city, res.district, res.detail, res.address_conf))
    return "\n".join(out)
