import { useState, useEffect } from 'react'
import { api } from './api'

export default function App() {
  const [tasks, setTasks] = useState([])
  const [ws, setWs] = useState(null)

  useEffect(() => {
    api.listTasks().then(setTasks)
    const socket = new WebSocket(`ws://${location.host}/ws`)
    socket.onopen = () => setWs(socket)
    return () => socket.close()
  }, [])

  return (
    <div style={{ display: 'flex', minHeight: '100vh', fontFamily: 'system-ui' }}>
      <aside style={{ width: 160, background: '#111827', color: '#fff', padding: 12 }}>
        <h3>mio-taskhub</h3>
        <nav><div>任务看板</div><div>夜间计划</div><div>Agent 状态</div></nav>
      </aside>
      <main style={{ flex: 1, padding: 16 }}>
        <h2>任务看板 <span style={{ fontSize: 12, color: '#16a34a' }}>● {ws ? '已连接' : '未连接'}</span></h2>
        <ul>
          {tasks.map(t => <li key={t.id}>{t.title} — <b>{t.state}</b></li>)}
        </ul>
      </main>
    </div>
  )
}
