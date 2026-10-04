"""黄金数据集：词族（同一键）、单例（自成一族）、边界用例。

FAMILIES: key(期望归并键) -> 该词族的全部形态。
SINGLETONS: 必须自成一族、不得与任何词族归并的词（过度归并陷阱）。
EDGE_CASES: 输入 -> 期望输出（大写缩写、非英文字符、标点、空串等）。
"""

FAMILIES: dict[str, list[str]] = {
    # ---- 规则变化词：名词复数/三单 ----------------------------------------
    "cat": ["cat", "cats", "CATS"],
    "dog": ["dog", "dogs"],
    "class": ["class", "classes"],
    "box": ["box", "boxes"],
    "dish": ["dish", "dishes"],
    "wish": ["wish", "wishes"],
    "buzz": ["buzz", "buzzes"],
    "bus": ["bus", "buses"],
    "case": ["case", "cases"],
    "city": ["city", "cities"],
    "gas": ["gas", "gases"],
    "potato": ["potato", "potatoes"],
    "leaf": ["leaf", "leaves"],
    "analysis": ["analysis", "analyses"],
    "universe": ["universe", "universes"],
    "fish": ["fish", "fishes"],  # 同形不变表：fishes 也归 fish
    # ---- 规则变化词：动词 --------------------------------------------------
    "study": ["study", "studies", "studied", "studying"],
    "play": ["play", "plays", "played", "playing"],
    "like": ["like", "likes", "liked", "liking"],
    "use": ["use", "uses", "used", "using"],
    "run": ["run", "runs", "running", "ran"],
    "stop": ["stop", "stops", "stopped", "stopping"],
    "plan": ["plan", "plans", "planned", "planning"],
    "work": ["work", "works", "worked", "working"],
    "organize": ["organize", "organizes", "organized", "organizing",
                 "organization", "organizations"],
    "act": ["act", "acts", "acted", "acting", "action", "actions"],
    "relate": ["relate", "relates", "related", "relating",
               "relation", "relations"],
    "generate": ["generate", "generates", "generated", "generating",
                 "generation", "generations"],
    "teach": ["teach", "teaches", "taught"],
    "meet": ["meet", "meets", "met", "meeting"],
    "see": ["see", "sees", "saw", "seen", "seeing"],
    "bring": ["bring", "brings", "brought", "bringing"],
    "think": ["think", "thinks", "thought", "thinking"],
    "leave": ["leave", "left", "leaving"],  # leaves 归 leaf（不规则表）
    # ---- 不规则词 ----------------------------------------------------------
    "be": ["be", "am", "is", "are", "was", "were", "been", "being"],
    "go": ["go", "goes", "went", "gone", "going"],
    "do": ["do", "does", "did", "done", "doing"],
    "have": ["have", "has", "had", "having"],
    "man": ["man", "men"],
    "woman": ["woman", "women"],
    "child": ["child", "children"],
    "ox": ["ox", "oxen"],
    "mouse": ["mouse", "mice"],
    "goose": ["goose", "geese"],
    "foot": ["foot", "feet"],
    "tooth": ["tooth", "teeth"],
    "person": ["person", "people"],
    # ---- 形容词/副词比较等级 ----------------------------------------------
    "good": ["good", "better", "best"],
    "bad": ["bad", "worse", "worst"],
    "far": ["far", "farther", "further", "farthest", "furthest"],
    "much": ["much", "more", "most", "many"],
    "fast": ["fast", "faster", "fastest"],
    "big": ["big", "bigger", "biggest"],
    "large": ["large", "larger", "largest"],
    "happy": ["happy", "happier", "happiest", "happily", "happiness"],
    "easy": ["easy", "easier", "easiest", "easily"],
    "quick": ["quick", "quicker", "quickest", "quickly"],
    "hard": ["hard", "harder", "hardest"],
    "early": ["early", "earlier", "earliest"],
    "high": ["high", "higher", "highest"],
    "dark": ["dark", "darker", "darkest", "darkness"],
    # ---- 代词 --------------------------------------------------------------
    "it": ["it", "its"],
    "her": ["her", "hers"],
    "our": ["our", "ours"],
    "your": ["your", "yours"],
    "their": ["their", "theirs"],
    # ---- 大写缩写 ----------------------------------------------------------
    "API": ["API", "APIs", "api"],
    "CD": ["CD", "CDs", "cd"],
    # ---- 非英文字符（带重音的拉丁词） --------------------------------------
    "café": ["café", "cafés"],
}

# 单例：自成一族，任何归并到其它词族的行为都计为“错误归并”。
SINGLETONS: list[str] = [
    # 封闭词（规则会误删词尾）
    "this", "his", "news", "lens", "physics", "economics",
    "hardly", "highly", "likely", "only",
    # 施事名词（-er 规则陷阱）
    "teacher", "worker", "player", "writer", "singer", "dancer",
    "speaker", "reader", "leader", "farmer", "manager", "builder",
    "printer", "painter", "driver", "runner", "actor", "doctor",
    # 词根相同但词族不同（派生陷阱）
    "organ", "hospital", "general", "generic",
    "university", "hospitality", "serious", "special",
    "thin", "hi", "new", "as",
    # 其它
    "the", "a", "an", "and", "us", "yes", "series", "species",
    "sheep", "deer", "moose", "evening", "thing", "during",
    "sacred", "learned", "beloved",
]

# 边界用例：输入 -> 期望输出
EDGE_CASES: dict[str, str] = {
    "": "",
    "   ": "",
    "!!!": "!!!",
    "123": "123",
    "abc123": "abc123",
    "3D": "3D",
    "COVID-19": "COVID-19",
    "don't": "don't",
    "iPhone": "iPhone",
    "McDonald": "McDonald",
    "IT": "IT",          # 缩写表优先于普通词 it
    "it": "it",
    "USA": "USA",
    "DOGS": "dog",       # 全大写但小写形态是普通词 -> 走规则
    "APIs": "API",
    "ＣＡＴＳ": "cat",    # 全角 -> NFKC -> CATS -> 普通词规则
    "ＡＰＩ": "API",      # 全角缩写
    "日本語": "日本語",
    "über": "über",
    "naïve": "naïve",
    "café": "café",
    "😀": "😀",
    "cats.": "cat",      # 句尾标点剥离
    "(dogs)": "dog",
    "running,": "run",
}

# 必须“分开”的陷阱对：(a, b) 归并键不得相同（抽样断言，全量由 pair 指标覆盖）
TRAP_PAIRS: list[tuple[str, str]] = [
    ("organ", "organization"),
    ("hospital", "hospitality"),
    ("general", "generation"),
    ("generic", "generation"),
    ("universe", "university"),
    ("this", "thin"),
    ("his", "hi"),
    ("news", "new"),
    ("hardly", "hard"),
    ("highly", "high"),
    ("likely", "like"),
    ("teacher", "teach"),
    ("worker", "work"),
    ("actor", "act"),
    ("doctor", "do"),
    ("as", "a"),
    ("us", "use"),
    ("series", "serious"),
    ("species", "special"),
]
