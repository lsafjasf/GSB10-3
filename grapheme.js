'use strict';

/**
 * grapheme.js — 字素簇（grapheme cluster）切分与按簇光标操作。
 *
 * 仅使用 Node.js 标准库：Intl.Segmenter（Unicode 标准附件 UAX #29 的
 * 字素簇边界算法实现）。覆盖：
 *   - 组合字符（Extend / SpacingMark，GB9/GB9a）
 *   - ZWJ 连接的表情序列（GB11，如 👨‍👩‍👧‍👦、🏳️‍🌈）
 *   - 变体选择符 VS1–VS16、补充变体选择符（GB9）
 *   - 区域指示符组成的旗标（GB12/GB13，如 🇨🇳）
 *   - CR LF 视为一簇（GB3）；控制/格式字符各自成簇（GB4/GB5）
 *
 * 光标位置一律用 UTF-16 码元偏移（与 JS 字符串下标一致），
 * 且保证始终落在簇边界上，绝不落在簇内部或代理对中间。
 */

const segmenter = new Intl.Segmenter('und', { granularity: 'grapheme' });

/** 正向切分：返回簇字符串数组。 */
function segment(text) {
  const clusters = [];
  for (const part of segmenter.segment(text)) clusters.push(part.segment);
  return clusters;
}

/** 返回所有簇边界（码元偏移，含 0 与 text.length），升序。 */
function boundaries(text) {
  const result = [0];
  for (const part of segmenter.segment(text)) {
    result.push(part.index + part.segment.length);
  }
  return result;
}

/** pos 之后（不含 pos）的下一个簇边界；已在末尾则返回 text.length。 */
function nextBoundary(text, pos) {
  for (const b of boundaries(text)) {
    if (b > pos) return b;
  }
  return text.length;
}

/** pos 之前（不含 pos）的上一个簇边界；已在开头则返回 0。 */
function prevBoundary(text, pos) {
  let prev = 0;
  for (const b of boundaries(text)) {
    if (b >= pos) return prev;
    prev = b;
  }
  return prev;
}

/** 正向逐簇切分（通过 nextBoundary 步进）。 */
function segmentForward(text) {
  const clusters = [];
  for (let p = 0; p < text.length; ) {
    const q = nextBoundary(text, p);
    clusters.push(text.slice(p, q));
    p = q;
  }
  return clusters;
}

/**
 * 反向逐簇切分（通过 prevBoundary 从末尾步进）。
 * 返回的数组按“从后往前”的顺序排列（最后一个簇在 index 0）。
 * 不变量：segmentBackward(t).reverse() 与 segmentForward(t) 完全一致，
 * 该性质在 selftest.js 中用随机文本做往返断言。
 */
function segmentBackward(text) {
  const clusters = [];
  let end = text.length;
  while (end > 0) {
    const start = prevBoundary(text, end);
    clusters.push(text.slice(start, end));
    end = start;
  }
  return clusters;
}

/** 把 offset 归一到 >= offset 的最近簇边界（插入文本后光标可能落在簇内）。 */
function normalizeOffset(text, offset) {
  if (offset >= text.length) return text.length;
  const bs = boundaries(text);
  for (const b of bs) {
    if (b >= offset) return b;
  }
  return text.length;
}

/**
 * 按簇移动/删除的迷你编辑器。
 * cursor 为码元偏移，始终在簇边界上。
 */
class Editor {
  constructor(text = '') {
    this.text = text;
    this.cursor = 0;
  }

  /** 光标在文本中的簇序号（左侧有多少个簇）。 */
  clusterIndex() {
    let n = 0;
    for (const b of boundaries(this.text)) {
      if (b >= this.cursor) return n;
      n += 1;
    }
    return n;
  }

  moveToStart() {
    this.cursor = 0;
    return this;
  }

  moveToEnd() {
    this.cursor = this.text.length;
    return this;
  }

  moveLeft() {
    this.cursor = prevBoundary(this.text, this.cursor);
    return this;
  }

  moveRight() {
    this.cursor = nextBoundary(this.text, this.cursor);
    return this;
  }

  /** 退格：删除光标左侧的整个簇。 */
  backspace() {
    if (this.cursor > 0) {
      const start = prevBoundary(this.text, this.cursor);
      this.text = this.text.slice(0, start) + this.text.slice(this.cursor);
      this.cursor = start;
    }
    return this;
  }

  /** Delete 键：删除光标右侧的整个簇，光标不动。 */
  deleteForward() {
    const end = nextBoundary(this.text, this.cursor);
    if (end > this.cursor) {
      this.text = this.text.slice(0, this.cursor) + this.text.slice(end);
    }
    return this;
  }

  /** 在光标处插入文本；插入后光标移到插入内容之后最近的簇边界。 */
  insert(s) {
    this.text = this.text.slice(0, this.cursor) + s + this.text.slice(this.cursor);
    this.cursor = normalizeOffset(this.text, this.cursor + s.length);
    return this;
  }

  /** 带光标标记的显示串：▏表示光标，| 表示簇边界，不可见字符转义为 <U+XXXX>。 */
  visualize() {
    return visualize(this.text, this.cursor);
  }
}

/** 把不可见/易混淆字符转义为 <U+XXXX>，便于在终端观察状态序列。 */
function escapeChar(ch) {
  const cp = ch.codePointAt(0);
  // Cf(格式,含 ZWJ/双向控制) Cc(控制,含 CR/LF) Cs(孤立代理项) Zl/Zp 及变体选择符
  if (/[\p{Cc}\p{Cf}\p{Cs}\p{Zl}\p{Zp}]/u.test(ch) || (cp >= 0xfe00 && cp <= 0xfe0f) || (cp >= 0xe0100 && cp <= 0xe01ef)) {
    return '<U+' + cp.toString(16).toUpperCase().padStart(4, '0') + '>';
  }
  return ch;
}

/** 用 | 标出簇边界、▏标出光标位置的可视化字符串。 */
function visualize(text, cursor) {
  let out = '';
  if (cursor === 0) out += '▏';
  const bs = boundaries(text);
  let prev = 0;
  for (let i = 1; i < bs.length; i += 1) {
    const b = bs[i];
    for (const ch of text.slice(prev, b)) out += escapeChar(ch);
    if (b === cursor) out += '▏';
    if (b < text.length) out += '|';
    prev = b;
  }
  return out;
}

module.exports = {
  segment,
  boundaries,
  nextBoundary,
  prevBoundary,
  segmentForward,
  segmentBackward,
  normalizeOffset,
  Editor,
  visualize,
  escapeChar,
};
