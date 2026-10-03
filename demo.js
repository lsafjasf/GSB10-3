'use strict';

/**
 * demo.js — 演示按簇的光标移动与删除，打印每一步之后的文本状态。
 * 图例：▏= 光标，| = 簇边界，<U+XXXX> = 不可见字符（ZWJ、变体选择符、双向控制符等）。
 * 运行：node demo.js
 */

const { Editor } = require('./grapheme.js');

function run(title, text, ops) {
  const ed = new Editor(text);
  console.log('== ' + title + ' ==');
  console.log('初始        ' + ed.visualize());
  for (const [label, op] of ops) {
    ed[op]();
    console.log(label.padEnd(10, ' ') + ' ' + ed.visualize());
  }
  console.log('');
}

// 场景 1：混合文本（ASCII + 组合字符 + ZWJ 家庭表情 + 旗标）
// 注意 "é" 是 e + U+0301 两个码点，但只占一个光标位。
run('场景1 混合文本：右移到末尾，再退格三次', 'Héllo 👨‍👩‍👧‍👦 🇨🇳!', [
  ['→ 右移', 'moveRight'],
  ['→ 右移', 'moveRight'],
  ['→ 右移', 'moveRight'],
  ['→ 右移', 'moveRight'],
  ['→ 右移', 'moveRight'],
  ['→ 右移', 'moveRight'],
  ['→ 右移', 'moveRight'],
  ['→ 右移', 'moveRight'],
  ['→ 右移', 'moveRight'],
  ['→ 右移', 'moveRight'],
  ['⌫ 退格', 'backspace'],
  ['⌫ 退格', 'backspace'],
  ['⌫ 退格', 'backspace'],
]);

// 场景 2：整面旗、整个 ZWJ 序列都是一次退格删完，不会拆出半个表情
run('场景2 整簇删除：旗标与家庭表情', '👍🏽🏳️‍🌈🇨🇳🇺🇸🇯🇵', [
  ['⇥ 到末尾', 'moveToEnd'],
  ['⌫ 退格', 'backspace'],
  ['⌫ 退格', 'backspace'],
  ['⌫ 退格', 'backspace'],
  ['⌫ 退格', 'backspace'],
  ['⌫ 退格', 'backspace'],
]);

// 场景 3：双向控制符各自成簇；Delete 键删光标右侧的簇
run('场景3 双向控制符文本（RLO/PDF）', 'ab‮cd‬', [
  ['→ 右移', 'moveRight'],
  ['→ 右移', 'moveRight'],
  ['→ 右移', 'moveRight'],
  ['⌫ 退格', 'backspace'],
  ['Del 前删', 'deleteForward'],
  ['Del 前删', 'deleteForward'],
]);

// 场景 4：插入导致合并时，光标归一到簇边界（不会停在 e 和重音符之间）
{
  const ed = new Editor('́x');
  console.log('== 场景4 在孤立组合符前插入字母 ==');
  console.log('初始        ' + ed.visualize());
  ed.insert('e');
  console.log('插入 "e"    ' + ed.visualize());
  ed.moveRight();
  console.log('→ 右移      ' + ed.visualize());
  ed.backspace();
  console.log('⌫ 退格      ' + ed.visualize());
  console.log('');
}
