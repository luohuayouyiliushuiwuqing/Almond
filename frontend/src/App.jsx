import { useEffect } from 'react'
import {
  useStore, loadRecords, loadProxyDomains, loadDomainConfig, detectProxy,
  startBackfill, startProxyPoll, applyPrefsTag, syncDomainRoute,
} from './store.js'
import { openModal, closeModal, useModal } from './ui.js'
import Toolbar from './Toolbar.jsx'
import Sidebar from './Sidebar.jsx'
import Board from './Board.jsx'
import DomainPage from './DomainPage.jsx'
import SelectBar from './SelectBar.jsx'
import ProxyPanel from './ProxyPanel.jsx'
import StoragePanel from './StoragePanel.jsx'
import ImportExportPanel from './ImportExportPanel.jsx'
import DetailModal from './DetailModal.jsx'
import RenameModal from './RenameModal.jsx'
import TagModal from './TagModal.jsx'

function StatusBar() {
  const s = useStore()
  return <div className={'statusbar ' + (s.status.kind || '')}>{s.status.text}</div>
}

export default function App() {
  const s = useStore()
  const modal = useModal()

  useEffect(() => {
    /* 路由先定视图：带 #/domain/xxx 打开时首屏直接是子页，不闪看板 */
    const onHash = () => { syncDomainRoute() }
    window.addEventListener('hashchange', onHash)
    syncDomainRoute()

    loadProxyDomains() // 先拿走代理域名集合，回来后自动 render 打标

    loadRecords()
      .then(() => detectProxy({ fill: true, announce: false }))
      .then(() => loadDomainConfig())
      .then(() => {
        applyPrefsTag() // 子页：套用该域名存的标签偏好（domainPage 进入时已 apply 过，这里幂等）
        startBackfill()
      })

    startProxyPoll()
    return () => window.removeEventListener('hashchange', onHash)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  let modalEl = null
  if (modal.type === 'proxy') modalEl = <ProxyPanel />
  else if (modal.type === 'storage') modalEl = <StoragePanel />
  else if (modal.type === 'import_export') modalEl = <ImportExportPanel />
  else if (modal.type === 'detail') modalEl = modal.payload ? <DetailModal record={modal.payload} /> : null
  else if (modal.type === 'rename') modalEl = modal.payload ? <RenameModal record={modal.payload} /> : null
  else if (modal.type === 'tag') modalEl = modal.payload ? <TagModal payload={modal.payload} /> : null

  return (
    <div className="app">
      <Toolbar />
      <main>
        <Sidebar />
        <div className="board-wrap">
          {s.domainPage ? <DomainPage /> : <Board />}
        </div>
      </main>
      <SelectBar />
      <StatusBar />
      {modalEl}
    </div>
  )
}
