'use strict';

/**
 * selftest.js — 字素簇切分与光标操作的自测。
 * 运行：node selftest.js
 * 失败时抛错并以非零码退出；全部通过时打印汇总。
 */

const assert = require('node:assert/strict');
const {
  segment,
  boundaries,
  nextBoundary,
  prevBoundary,
  segmentForward,
  segmentBackward,
  Editor,
} = require('./grapheme.js');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log('ok - ' + name);
}

/* ---------- 1. 切分：组合字符 / ZWJ / 变体选择符 / 旗标 / 控制符 ---------- */

test('纯 ASCII 逐字符成簇', () => {
  assert.deepEqual(segment('hello'), ['h', 'e', 'l', 'l', 'o']);
  assert.deepEqual(boundaries('hello'), [0, 1, 2, 3, 4, 5]);
});

test('组合字符（NFC 与 NFD 等价）', () => {
  assert.deepEqual(segment('é'), ['é']);            // e + U+0301
  assert.deepEqual(segment('é'), ['é']);                 // 预组合字符
  assert.deepEqual(segment('á̧'), ['á̧']); // 叠两个组合符
  assert.deepEqual(segment('कि'), ['कि']);          // 天城文：辅音 + 元音符（SpacingMark）
});

test('ZWJ 连接的表情序列是一簇', () => {
  assert.deepEqual(segment('👨‍👩‍👧‍👦'), ['👨‍👩‍👧‍👦']);
  assert.deepEqual(segment('🏳️‍🌈'), ['🏳️‍🌈']);   // 白旗 + VS16 + ZWJ + 彩虹
  assert.deepEqual(segment('👍🏽'), ['👍🏽']);            // 肤色修饰符
});

test('变体选择符附着在前一字符上', () => {
  assert.deepEqual(segment('✈️'), ['✈️']);       // U+2708 U+FE0F
  assert.deepEqual(segment('❤️'), ['❤️']);       // U+2764 U+FE0F
  assert.deepEqual(segment('邊󠄀'), ['邊󠄀']); // U+908A U+E0100（IVS）
});

test('区域指示符两两组成旗标', () => {
  assert.deepEqual(segment('🇨🇳'), ['🇨🇳']);
  assert.deepEqual(segment('🇨🇳🇺🇸'), ['🇨🇳', '🇺🇸']);
  // 奇数个 RI：前两个成旗，第三个落单
  assert.deepEqual(segment('🇨🇳🇺🇸🇯🇵'), ['🇨🇳', '🇺🇸', '🇯🇵']);
});

test('CR LF 是一簇，控制/格式字符各自成簇', () => {
  assert.deepEqual(segment('\r\n'), ['\r\n']);
  assert.deepEqual(segment('a\r\nb'), ['a', '\r\n', 'b']);
  // 双向控制符 U+202E (RLO) / U+202C (PDF)：各自独立成簇，不吞并相邻字符
  assert.deepEqual(segment('ab‮cd‬'), ['a', 'b', '‮', 'c', 'd', '‬']);
});

/* ---------- 2. 光标移动与删除：逐次操作后的文本状态序列 ---------- */

test('光标右移按簇步进（混合文本）', () => {
  const ed = new Editor('A👨‍👩‍👧‍👦B🇨🇳');
  const positions = [ed.cursor];
  for (let i = 0; i < 10; i += 1) { ed.moveRight(); positions.push(ed.cursor); }
  // A=1 码元，家庭表情=11 码元，B=1，🇨🇳=4；到末尾后继续右移是空操作
  assert.deepEqual(positions, [0, 1, 12, 13, 17, 17, 17, 17, 17, 17, 17]);
});

test('光标左移与右移对称', () => {
  const ed = new Editor('A👨‍👩‍👧‍👦B🇨🇳');
  ed.cursor = ed.text.length;
  const left = [];
  for (let i = 0; i < 4; i += 1) { ed.moveLeft(); left.push(ed.cursor); }
  assert.deepEqual(left, [13, 12, 1, 0]);
  ed.moveLeft(); // 到顶后空操作
  assert.equal(ed.cursor, 0);
});

test('退格按整簇删除，文本状态序列正确', () => {
  const ed = new Editor('A👨‍👩‍👧‍👦B🇨🇳');
  ed.cursor = ed.text.length;
  const states = [];
  for (let i = 0; i < 6; i += 1) { ed.backspace(); states.push(ed.text); }
  assert.deepEqual(states, [
    'A👨‍👩‍👧‍👦B',   // 删掉 🇨🇳（一次退格删整面旗，不是半个 RI）
    'A👨‍👩‍👧‍👦',    // 删掉 B
    'A',          // 删掉整个 👨‍👩‍👧‍👦（不会拆成 👨‍👩‍👧 等中间态）
    '',           // 删掉 A
    '',           // 空文本上空操作
    '',
  ]);
  assert.equal(ed.cursor, 0);
});

test('Delete 键删除光标右侧簇，光标不动', () => {
  const ed = new Editor('x🏳️‍🌈y');
  ed.moveRight(); // 光标在 x 之后
  ed.deleteForward();
  assert.equal(ed.text, 'xy');
  assert.equal(ed.cursor, 1);
  ed.deleteForward();
  assert.equal(ed.text, 'x');
});

test('双向控制符文本中的移动与删除', () => {
  const ed = new Editor('ab‮cd‬');
  const positions = [0];
  for (let i = 0; i < 6; i += 1) { ed.moveRight(); positions.push(ed.cursor); }
  assert.deepEqual(positions, [0, 1, 2, 3, 4, 5, 6]); // 每个控制符是一簇，各占 1 码元
  ed.backspace(); // 删 U+202C
  assert.equal(ed.text, 'ab‮cd');
  ed.cursor = 2;
  ed.deleteForward(); // 删 U+202E
  assert.equal(ed.text, 'abcd');
});

test('插入后光标归一到簇边界', () => {
  const ed = new Editor('́'); // 只有一个组合符
  ed.insert('e');            // 变成 e + U+0301，是同一簇
  assert.equal(ed.text, 'é');
  assert.equal(ed.cursor, 2); // 光标在簇尾，不会停在 e 与组合符之间
});

/* ---------- 3. 反向切分与正向一致：随机文本往返断言 ---------- */

// 可复现的伪随机数（mulberry32）
function mulberry32(seed) {
  let a = seed >>> 0;
  return function rand() {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// 原子池：既有完整簇，也有“游离”的组合符/ZWJ/RI，
// 随机拼接后会产生各种合并情形，专门考验切分与光标逻辑。
const ATOMS = [
  'a', 'Z', '0', ' ', '你', '好',                 // ASCII / CJK
  '́', '̧', '̃',                   // 游离组合符
  'e', 'é', 'क', 'ि', 'देवनागरी',
  '‍',                                         // 游离 ZWJ
  '️', '󠄀',                                 // 变体选择符
  '👍', '🏽', '👨', '👩', '👧', '👦', '🌈', '🏳', '❤', '✈',
  '🇨', '🇳', '🇺🇸', '🇯', '🇵',                 // 区域指示符（单个与成对）
  '👨‍👩‍👧‍👦', '🏳️‍🌈', '👍🏽',                       // 预制 ZWJ 序列
  '\r\n', '\n', '\t',
  '‮', '‬', '⁦', '⁩', // 双向控制符
  '�', '\uD800', '\uDFFF',                       // 替换符与孤立代理项
];

function randomText(rand, maxAtoms) {
  const n = 1 + Math.floor(rand() * maxAtoms);
  let s = '';
  for (let i = 0; i < n; i += 1) s += ATOMS[Math.floor(rand() * ATOMS.length)];
  return s;
}

test('随机文本：反向切分与正向一致，且拼接还原（往返断言）', () => {
  const rand = mulberry32(20261004);
  for (let round = 0; round < 1000; round += 1) {
    const text = randomText(rand, 40);
    const fwd = segmentForward(text);
    const bwd = segmentBackward(text);
    // 往返：反向切分倒过来必须等于正向切分
    assert.deepEqual(bwd.slice().reverse(), fwd, 'round ' + round + ': ' + JSON.stringify(text));
    // 与 Intl.Segmenter 直接切分一致
    assert.deepEqual(segment(text), fwd);
    // 拼接还原：任何文本都能被簇无重叠无遗漏地拼回（bwd 是从后往前的顺序，先反转）
    assert.equal(fwd.join(''), text);
    assert.equal(bwd.slice().reverse().join(''), text);
    // 边界首尾正确
    const bs = boundaries(text);
    assert.equal(bs[0], 0);
    assert.equal(bs[bs.length - 1], text.length);
  }
});

test('随机文本：光标从尾往左走与从头往右走经过相同边界', () => {
  const rand = mulberry32(12345);
  for (let round = 0; round < 300; round += 1) {
    const text = randomText(rand, 30);
    const rightPath = [0];
    for (let p = 0; p < text.length; ) { p = nextBoundary(text, p); rightPath.push(p); }
    const leftPath = [text.length];
    for (let p = text.length; p > 0; ) { p = prevBoundary(text, p); leftPath.push(p); }
    assert.deepEqual(leftPath.reverse(), rightPath, 'round ' + round);
    assert.deepEqual(rightPath, boundaries(text));
  }
});

test('随机文本：从头退格到空，每步都删一个完整的簇', () => {
  // UAX #29 的所有不断行规则都只依赖左侧上下文，
  // 因此从末尾退格不会改变剩余前缀的切分：ed.text 恒等于剩余簇的拼接。
  const rand = mulberry32(777);
  for (let round = 0; round < 200; round += 1) {
    const original = randomText(rand, 20);
    const ed = new Editor(original);
    ed.cursor = original.length;
    const remaining = segment(original);
    while (remaining.length > 0) {
      const lastCluster = remaining[remaining.length - 1];
      const before = ed.text;
      ed.backspace();
      remaining.pop();
      // 删掉的正好是末尾那一整个簇（不多不少）
      assert.equal(before.slice(before.length - lastCluster.length), lastCluster);
      assert.equal(ed.text, remaining.join(''), 'round ' + round);
      assert.equal(ed.cursor, ed.text.length);
    }
    assert.equal(ed.text, '');
    assert.equal(ed.cursor, 0);
  }
});

/* ---------- 4. 边界用例 ---------- */

test('空字符串', () => {
  assert.deepEqual(segment(''), []);
  assert.deepEqual(boundaries(''), [0]);
  const ed = new Editor('');
  ed.moveLeft(); ed.moveRight(); ed.backspace(); ed.deleteForward();
  assert.equal(ed.text, '');
  assert.equal(ed.cursor, 0);
});

test('单字符与单簇文本', () => {
  const ed = new Editor('🇨🇳');
  ed.moveRight(); ed.moveRight(); // 只有一簇，第二次是空操作
  assert.equal(ed.cursor, 4);
  ed.backspace();
  assert.equal(ed.text, '');
});

test('只有组合符 / 只有 ZWJ 的退化文本', () => {
  assert.deepEqual(segment('̧́'), ['̧́']);   // 组合符附着到行首，自成一簇
  assert.deepEqual(segment('‍‍'), ['‍‍']);   // GB9：ZWJ 前不断行，连续 ZWJ 同簇
  const ed = new Editor('̧́');
  ed.cursor = ed.text.length;
  ed.backspace();
  assert.equal(ed.text, '');
});

test('孤立代理项各自成簇且可往返', () => {
  const text = 'a\uD800b\uDFFF';
  const clusters = segment(text);
  assert.equal(clusters.join(''), text);
  assert.deepEqual(segmentBackward(text).reverse(), clusters);
});

test('长串 RI 的奇偶配对', () => {
  const five = '🇦🇧🇨🇩🇪'; // 5 个 RI
  const clusters = segment(five);
  assert.equal(clusters.length, 3); // 🇦🇧 🇨🇩 🇪
  const ed = new Editor(five);
  ed.cursor = five.length;
  ed.backspace(); // 删掉落单的 🇪
  assert.equal(ed.text, '🇦🇧🇨🇩');
  ed.backspace(); // 删掉整面旗 🇨🇩
  assert.equal(ed.text, '🇦🇧');
});

console.log('\n全部 ' + passed + ' 项测试通过。');
