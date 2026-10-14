/* 模态状态总线：组件用 openModal(type, payload) 触发，App 统一渲染对应弹层。 */
import { useSyncExternalStore } from 'react'

let modal = { type: null, payload: null }
let version = 0
const listeners = new Set()
function subscribe(cb) {
  listeners.add(cb)
  return () => listeners.delete(cb)
}
export function openModal(type, payload) {
  modal = { type, payload: payload || null }
  version++
  listeners.forEach((l) => l())
}
export function closeModal() {
  modal = { type: null, payload: null }
  version++
  listeners.forEach((l) => l())
}
export function useModal() {
  useSyncExternalStore(subscribe, () => version, () => version)
  return modal
}
