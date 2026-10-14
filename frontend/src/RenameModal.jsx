import { useState } from 'react'
import { useStore, renameRecord } from './store.js'
import { closeModal } from './ui.js'

export default function RenameModal({ record }) {
  const [val, setVal] = useState(record.title || '')

  const submit = async () => {
    const title = val.trim()
    closeModal()
    if (title !== (record.title || '')) await renameRecord(record.url, title)
  }

  return (
    <div className="modal-mask" onClick={(e) => { if (e.target === e.currentTarget) closeModal() }}>
      <div className="modal">
        <div className="modal-title">重命名</div>
        <div className="detail-domain">{record.url}</div>
        <input
          className="input"
          autoFocus
          value={val}
          onChange={(e) => setVal(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') submit(); if (e.key === 'Escape') closeModal() }}
        />
        <div className="modal-actions">
          <button className="btn-ghost" onClick={closeModal}>取消</button>
          <button className="btn-primary" onClick={submit}>保存</button>
        </div>
      </div>
    </div>
  )
}
