/* 极简事件总线。**只**承载 render / updateCard 两条回调边；
   其余跨模块边一律 hoisted function 声明（docs/CLAUDE.md 循环 import 规则）。
   订阅（on）只允许在 main.js init() 内调用——每个事件恰一个订阅者：
   "render" → board.render，"updateCard" → cards.updateCard。 */
const handlers = new Map();

export function on(name, fn) {
  handlers.set(name, fn);
}

export function emit(name, ...args) {
  const fn = handlers.get(name);
  if (fn) fn(...args);
}
