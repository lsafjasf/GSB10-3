# -*- coding: utf-8 -*-
"""演示：多音字候选样例 + 单字/词组模式对比 + 边界用例。
用法：python3 demo.py            打印到终端
     python3 demo.py samples/   同时写入 samples/ 目录
"""
import os
import sys

from pinyinlib import candidates, convert_str, explain, heteronym

OUT = []


def show(line=''):
    print(line)
    OUT.append(line)


def main():
    show('== 1. 多音字候选列表（含优先级依据）==')
    for ch in ['行', '长', '重', '乐', '差', '着']:
        show('「%s」候选：' % ch)
        for c in candidates(ch):
            show('  %d. %-6s (%s)' % (c['rank'], c['pinyin'], c['basis']))
    show()
    show('== 2. 词组优先 vs 单字默认 ==')
    for text in ['银行', '行走', '长大', '长江', '重量', '重复', '音乐', '快乐']:
        show('%-4s 词组模式: %-14s 单字模式: %s'
             % (text, convert_str(text, mode='phrase'),
                convert_str(text, mode='char')))
    show()
    show('== 3. 带声调 / 不带声调 ==')
    text = '中国人民银行'
    show('原文:   %s' % text)
    show('带调:   %s' % convert_str(text, style='marks'))
    show('不带调: %s' % convert_str(text, style='plain'))
    show('数字调: %s' % convert_str(text, style='numbers'))
    show()
    show('== 4. 逐字候选（heteronym）==')
    for text in ['银行', '行走']:
        show('%s -> %s' % (text, heteronym(text)))
    show()
    show('== 5. 定音依据（explain）==')
    for row in explain('他在银行行走'):
        show('  %s -> %-6s %s' % (row['text'], row['pinyin'], row['reason']))
    show()
    show('== 6. 边界用例 ==')
    show('空串:        %r' % convert_str(''))
    show('纯英文数字:  %r' % convert_str('Hello 123'))
    show('全角符号:    %r' % convert_str('你好，世界！'))
    show('全角转半角:  %r' % convert_str('ＡＢＣ中文１２３', normalize=True))
    show('未收录汉字:  %r' % convert_str('我龘你'))

    if len(sys.argv) > 1:
        os.makedirs(sys.argv[1], exist_ok=True)
        path = os.path.join(sys.argv[1], 'demo_output.txt')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(OUT) + '\n')
        print('已写入 %s' % path, file=sys.stderr)


if __name__ == '__main__':
    main()
