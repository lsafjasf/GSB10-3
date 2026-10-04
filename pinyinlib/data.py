# -*- coding: utf-8 -*-
"""拼音规则数据表（仅标准库，数据内嵌，无需任何第三方依赖）。

读音记号：音节末尾数字即声调，1-4 为四声，5 为轻声，例如 xing2、de5、lü4。
- CHAR_TABLE：单字 -> 候选读音列表，列表顺序即单字模式的优先级
  （现代汉语语料使用频率排序，频率相近时以词表收录数为辅助依据）。
- PHRASE_TABLE：词组 -> 逐字读音，词组模式中通过最长匹配命中。
  词表是“上下文规则”，优先级高于单字频率。

这是一份精选词表（覆盖常见多音字与对拍样例），扩充时只需追加条目，
tests/test_pinyin.py 会自动校验词组中的每个字都在单字表内。
"""

_CHAR_RAW = """
# === 多音字（按常用读音频率排序，首位为单字模式默认音）===
行 xing2 hang2
长 chang2 zhang3
重 zhong4 chong2
乐 le4 yue4
的 de5 di2 di4
地 di4 de5
得 de2 de5 dei3
着 zhe5 zhuo2 zhao2 zhao1
了 le5 liao3
都 dou1 du1
还 hai2 huan2
中 zhong1 zhong4
为 wei4 wei2
好 hao3 hao4
少 shao3 shao4
难 nan2 nan4
便 bian4 pian2
教 jiao4 jiao1
觉 jue2 jiao4
只 zhi3 zhi1
种 zhong3 zhong4
分 fen1 fen4
干 gan1 gan4
几 ji3 ji1
系 xi4 ji4
假 jia3 jia4
将 jiang1 jiang4
降 jiang4 xiang2
空 kong1 kong4
量 liang4 liang2
没 mei2 mo4
强 qiang2 qiang3 jiang4
省 sheng3 xing3
数 shu4 shu3 shuo4
说 shuo1 shui4
相 xiang1 xiang4
应 ying1 ying4
转 zhuan3 zhuan4
传 chuan2 zhuan4
朝 chao2 zhao1
曾 ceng2 zeng1
差 cha4 cha1 chai1 ci1
场 chang3 chang2
处 chu4 chu3
当 dang1 dang4
调 diao4 tiao2
发 fa1 fa4
更 geng4 geng1
给 gei3 ji3
冠 guan1 guan4
号 hao4 hao2
喝 he1 he4
和 he2 he4 huo2
华 hua2 hua4
会 hui4 kuai4
间 jian1 jian4
角 jiao3 jue2
结 jie2 jie1
解 jie3 jie4 xie4
看 kan4 kan1
壳 ke2 qiao4
落 luo4 la4 lao4
累 lei4 lei3 lei2
露 lu4 lou4
绿 lü4 lu4
论 lun4 lun2
埋 mai2 man2
蒙 meng2 meng3 meng1
模 mo2 mu2
宁 ning2 ning4
弄 nong4 long4
片 pian4 pian1
漂 piao1 piao4 piao3
奇 qi2 ji1
悄 qiao1 qiao3
曲 qu3 qu1
塞 sai1 sai4 se4
散 san4 san3
似 si4 shi4
宿 su4 xiu3 xiu4
提 ti2 di1
挑 tiao1 tiao3
吐 tu3 tu4
鲜 xian1 xian3
校 xiao4 jiao4
血 xue4 xie3
咽 yan1 yan4 ye4
要 yao4 yao1
载 zai4 zai3
扎 zha1 zha2 za1
占 zhan4 zhan1
正 zheng4 zheng1
钻 zuan4 zuan1
作 zuo4 zuo1
坊 fang1 fang2
# === 单音字（对拍与样例所需常用字）===
我 wo3
你 ni3
世 shi4
界 jie4
银 yin2
走 zou3
江 jiang1
快 kuai4
音 yin1
目 mu4
确 que4
睡 shui4
国 guo2
奖 jiang3
首 shou3
是 shi4
钱 qian2
学 xue2
方 fang1
书 shu1
室 shi4
有 you3
净 jing4
活 huo2
乎 hu1
个 ge4
期 qi1
如 ru2
天 tian1
困 kun4
灾 zai1
勉 mian3
反 fan3
话 hua4
信 xin4
该 gai1
用 yong4
身 shen1
记 ji4
阳 yang2
代 dai4
经 jing1
不 bu4
多 duo1
出 chu1
理 li3
到 dao4
查 cha2
现 xian4
头 tou2
加 jia1
予 yu3
水 shui3
平 ping2
山 shan1
计 ji4
隔 ge2
度 du4
色 se4
实 shi2
放 fang4
见 jian4
守 shou3
贝 bei4
劳 lao2
积 ji1
面 mian4
古 gu3
型 xing2
样 yang4
静 jing4
可 ke3
堂 tang2
亮 liang4
怪 guai4
歌 ge1
弯 wan1
步 bu4
文 wen2
舍 she4
高 gao1
防 fang2
战 zhan4
呕 ou3
新 xin1
液 ye4
喉 hou2
呜 wu1
求 qiu2
挣 zheng1
月 yue4
石 shi2
研 yan2
工 gong1
大 da4
一 yi1
子 zi3
语 yu3
怨 yuan4
对 dui4
包 bao1
复 fu4
宜 yi2
节 jie2
人 ren2
民 min2
他 ta1
在 zai4
"""

# 词组 -> 逐字读音（5 表示轻声）。词表即“按上下文定音”的规则。
_PHRASE_RAW = """
银行 yin2 hang2
行走 xing2 zou3
长大 zhang3 da4
长江 chang2 jiang1
重量 zhong4 liang4
重复 chong2 fu4
快乐 kuai4 le4
音乐 yin1 yue4
目的 mu4 di4
的确 di2 que4
觉得 jue2 de5
睡觉 shui4 jiao4
中国 zhong1 guo2
中奖 zhong4 jiang3
为了 wei4 le5
作为 zuo4 wei2
还是 hai2 shi4
还钱 huan2 qian2
首都 shou3 du1
都是 dou1 shi4
了解 liao3 jie3
数学 shu4 xue2
数一数 shu3 yi1 shu3
方便 fang1 bian4
便宜 pian2 yi5
教书 jiao1 shu1
教室 jiao4 shi4
只有 zhi3 you3
一只 yi1 zhi1
种子 zhong3 zi5
种地 zhong4 di4
干净 gan1 jing4
干活 gan4 huo2
几乎 ji1 hu1
几个 ji3 ge4
假期 jia4 qi1
假如 jia3 ru2
天空 tian1 kong1
空地 kong4 di4
没有 mei2 you3
困难 kun4 nan2
灾难 zai1 nan4
强大 qiang2 da4
勉强 mian3 qiang3
省会 sheng3 hui4
反省 fan3 xing3
说话 shuo1 hua4
相信 xiang1 xin4
相片 xiang4 pian4
应该 ying1 gai1
应用 ying4 yong4
转身 zhuan3 shen1
传说 chuan2 shuo1
传记 zhuan4 ji4
朝阳 zhao1 yang2
朝代 chao2 dai4
曾经 ceng2 jing1
差不多 cha4 bu5 duo1
出差 chu1 chai1
处理 chu3 li3
到处 dao4 chu4
调查 diao4 cha2
调节 tiao2 jie2
发现 fa1 xian4
头发 tou2 fa5
更加 geng4 jia1
给予 ji3 yu3
喝水 he1 shui3
和平 he2 ping2
华山 hua4 shan1
会计 kuai4 ji4
中间 zhong1 jian1
间隔 jian4 ge2
角度 jiao3 du4
角色 jue2 se4
结实 jie1 shi5
解放 jie3 fang4
看见 kan4 jian4
看守 kan1 shou3
贝壳 bei4 ke2
地壳 di4 qiao4
劳累 lao2 lei4
积累 ji1 lei3
露水 lu4 shui3
露面 lou4 mian4
绿色 lü4 se4
论语 lun2 yu3
埋怨 man2 yuan4
蒙古 meng3 gu3
模型 mo2 xing2
模样 mu2 yang4
宁静 ning2 jing4
宁可 ning4 ke3
弄堂 long4 tang2
漂亮 piao4 liang5
奇怪 qi2 guai4
奇数 ji1 shu4
歌曲 ge1 qu3
弯曲 wan1 qu1
散步 san4 bu4
散文 san3 wen2
似乎 si4 hu1
似的 shi4 de5
宿舍 su4 she4
提高 ti2 gao1
提防 di1 fang5
挑战 tiao3 zhan4
呕吐 ou3 tu4
新鲜 xin1 xian1
学校 xue2 xiao4
校对 jiao4 dui4
血液 xue4 ye4
咽喉 yan1 hou2
呜咽 wu1 ye4
要求 yao1 qiu2
载重 zai4 zhong4
记载 ji4 zai3
包扎 bao1 za1
挣扎 zheng1 zha2
正月 zheng1 yue4
钻石 zuan4 shi2
钻研 zuan1 yan2
作坊 zuo1 fang5
工作 gong1 zuo4
世界 shi4 jie4
你好 ni3 hao3
"""


def _parse_char_table(raw):
    table = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = line.split()
        table[parts[0]] = parts[1:]
    return table


def _parse_phrase_table(raw):
    table = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = line.split()
        table[parts[0]] = tuple(parts[1:])
    return table


CHAR_TABLE = _parse_char_table(_CHAR_RAW)
PHRASE_TABLE = _parse_phrase_table(_PHRASE_RAW)
