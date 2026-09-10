import { render } from 'preact'
import { useEffect, useState } from 'preact/hooks'
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Database,
  FileCheck2,
  LayoutDashboard,
  LogOut,
  Menu,
  Play,
  RefreshCw,
  Search,
  ShieldCheck,
  X,
} from 'lucide-preact'
import '@fontsource-variable/inter'
import './styles.css'

const money = (value) =>
  new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    maximumFractionDigits: 0,
  }).format(value / 100)
const shortId = (value) => `${value.slice(0, 12)}...`

function Badge({ value }) {
  const tone = ['HOLD', 'P1', 'OPEN', 'REOPENED'].includes(value)
    ? 'danger'
    : ['P2', 'INVESTIGATING', 'PENDING_APPROVAL'].includes(value)
      ? 'warning'
      : ['CLOSE', 'COMPLETED', 'RESOLVED'].includes(value)
        ? 'success'
        : 'neutral'
  return <span class={`badge ${tone}`}>{value.replaceAll('_', ' ')}</span>
}

function Login({ onLogin }) {
  const [error, setError] = useState('')
  const choose = async (token) => {
    try {
      setError('')
      await onLogin(token)
    } catch (e) {
      setError(e.message)
    }
  }
  return (
    <div class="login">
      <div class="login-card">
        <div class="logo">CT</div>
        <h1>Control Tower</h1>
        <p>Choose a workspace role to continue.</p>
        <div class="role-options">
          <button class="role-option" onClick={() => choose('operator-demo')}>
            <div class="role-icon">
              <Activity size={19} />
            </div>
            <div>
              <strong>Operations</strong>
              <span>Investigate and resolve exceptions</span>
            </div>
          </button>
          <button class="role-option" onClick={() => choose('approver-demo')}>
            <div class="role-icon approver">
              <ShieldCheck size={19} />
            </div>
            <div>
              <strong>Approver</strong>
              <span>Review approvals and decide close</span>
            </div>
          </button>
        </div>
        {error && <div class="form-error">{error}</div>}
        <small>Your selection stays active until you log out.</small>
      </div>
    </div>
  )
}

function App() {
  const [token, setToken] = useState(
    () => localStorage.getItem('controlTowerToken') || '',
  )
  const [user, setUser] = useState(null)
  const [restoring, setRestoring] = useState(Boolean(token))
  const [exceptions, setExceptions] = useState([])
  const [ingestion, setIngestion] = useState([])
  const [reconciliation, setReconciliation] = useState([])
  const [closes, setCloses] = useState([])
  const [selected, setSelected] = useState(null)
  const [actions, setActions] = useState([])
  const [status, setStatus] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState('')
  const [mobileNav, setMobileNav] = useState(false)
  const [view, setView] = useState('overview')

  const api = async (path, options = {}, authToken = token) => {
    const response = await fetch(path, {
      ...options,
      headers: {
        Authorization: `Bearer ${authToken}`,
        'Content-Type': 'application/json',
        ...options.headers,
      },
    })
    const type = response.headers.get('content-type') || ''
    const body = type.includes('application/json')
      ? await response.json()
      : await response.text()
    if (!response.ok)
      throw new Error(body.detail || body || response.statusText)
    return body
  }
  const login = async (value) => {
    const principal = await api('/api/me', {}, value)
    localStorage.setItem('controlTowerToken', value)
    setToken(value)
    setUser(principal)
  }
  const logout = () => {
    localStorage.removeItem('controlTowerToken')
    setToken('')
    setUser(null)
  }
  const refresh = async () => {
    try {
      const query = status ? `?status=${status}` : ''
      const [queue, ingestions, reconciliations, decisions] = await Promise.all(
        [
          api(`/api/exceptions${query}`),
          api('/api/ingestion-runs'),
          api('/api/reconciliation-runs'),
          api('/api/close-decisions'),
        ],
      )
      setExceptions(queue)
      setIngestion(ingestions)
      setReconciliation(reconciliations)
      setCloses(decisions)
      setNotice('')
    } catch (e) {
      setNotice(e.message)
    }
  }
  useEffect(() => {
    if (user) refresh()
  }, [user, status])
  useEffect(() => {
    if (!token || user) return
    api('/api/me', {}, token)
      .then(setUser)
      .catch(() => {
        localStorage.removeItem('controlTowerToken')
        setToken('')
      })
      .finally(() => setRestoring(false))
  }, [])
  const run = async (name, task) => {
    setBusy(name)
    try {
      await task()
      await refresh()
    } catch (e) {
      setNotice(e.message)
    } finally {
      setBusy('')
    }
  }
  const ingest = () =>
    run('ingest', () =>
      api('/api/ingestion-runs', {
        method: 'POST',
        body: JSON.stringify({ directory: 'development' }),
      }),
    )
  const reconcile = () =>
    run('reconcile', () =>
      api('/api/reconciliation-runs', { method: 'POST', body: '{}' }),
    )
  const decideClose = () =>
    run('close', () => {
      if (!ingestion[0] || !reconciliation[0])
        throw new Error('Ingest and reconcile before deciding close.')
      return api('/api/close-decisions', {
        method: 'POST',
        body: JSON.stringify({
          ingestion_run_key: ingestion[0].run_key,
          reconciliation_run_key: reconciliation[0].run_key,
        }),
      })
    })
  const openException = async (item) => {
    setSelected(item)
    setActions(await api(`/api/exceptions/${item.exception_id}/actions`))
  }
  const exceptionAction = async (action) => {
    const reason = prompt('Reason for this action')
    if (!reason) return
    await run(action, () =>
      api(`/api/exceptions/${selected.exception_id}/${action}`, {
        method: 'POST',
        body: JSON.stringify({ reason }),
      }),
    )
    setSelected(null)
  }
  if (restoring)
    return (
      <div class="app-loading">
        <div class="logo">CT</div>
        <span>Restoring workspace...</span>
      </div>
    )
  if (!user) return <Login onLogin={login} />
  const close = closes[0]
  const titles = {
    overview: [
      'Operations overview',
      'Monitor today’s reconciliation controls',
    ],
    exceptions: [
      'Exceptions',
      'Investigate, assign, and resolve reconciliation breaks',
    ],
    runs: [
      'Control runs',
      'Review ingestion, reconciliation, and close history',
    ],
  }
  const navigate = (next) => {
    setView(next)
    setMobileNav(false)
  }
  return (
    <div class="app-shell">
      <aside class={mobileNav ? 'sidebar open' : 'sidebar'}>
        <div class="brand">
          <div class="logo">CT</div>
          <div>
            <strong>Control Tower</strong>
            <span>Co-lending ops</span>
          </div>
          <button
            class="icon-button mobile-only"
            onClick={() => setMobileNav(false)}
          >
            <X size={18} />
          </button>
        </div>
        <nav>
          <span class="nav-heading">Workspace</span>
          <button
            class={view === 'overview' ? 'active' : ''}
            onClick={() => navigate('overview')}
          >
            <LayoutDashboard />
            Overview
          </button>
          <button
            class={view === 'exceptions' ? 'active' : ''}
            onClick={() => navigate('exceptions')}
          >
            <AlertTriangle />
            Exceptions <em>{exceptions.length}</em>
          </button>
          <button
            class={view === 'runs' ? 'active' : ''}
            onClick={() => navigate('runs')}
          >
            <Activity />
            Control runs
          </button>
        </nav>
        <div class="profile">
          <div class="avatar">{user.actor.slice(0, 2).toUpperCase()}</div>
          <div>
            <strong>{user.actor}</strong>
            <span>{user.role}</span>
          </div>
          <button class="icon-button" aria-label="Log out" onClick={logout}>
            <LogOut size={16} />
          </button>
        </div>
      </aside>
      <main>
        <header>
          <button
            class="icon-button mobile-only"
            onClick={() => setMobileNav(true)}
          >
            <Menu />
          </button>
          <div>
            <h2>{titles[view][0]}</h2>
            <span>{titles[view][1]}</span>
          </div>
          <div class="header-actions">
            <button class="button" onClick={refresh}>
              <RefreshCw size={15} />
              Refresh
            </button>
            {view !== 'exceptions' && (
              <button class="button primary" onClick={reconcile}>
                <Play size={15} />
                Run reconciliation
              </button>
            )}
          </div>
        </header>
        <div class="page">
          {notice && (
            <div class="notice">
              <AlertTriangle size={17} />
              {notice}
              <button onClick={() => setNotice('')}>
                <X size={15} />
              </button>
            </div>
          )}
          {view === 'overview' && (
            <>
              <section class="hero">
                <div>
                  <p>CONTROL STATUS</p>
                  <h1>
                    {close ? (
                      <>
                        Latest decision:{' '}
                        <span
                          class={close.outcome === 'HOLD' ? 'red' : 'green'}
                        >
                          {close.outcome}
                        </span>
                      </>
                    ) : (
                      'No close decision yet'
                    )}
                  </h1>
                  <span>
                    {close
                      ? `${close.blockers.length} blockers across ${close.scorecard.accepted_count.toLocaleString('en-IN')} decisions`
                      : 'Complete ingestion and reconciliation to calculate close.'}
                  </span>
                </div>
                <button
                  class="button primary"
                  onClick={decideClose}
                  disabled={busy === 'close'}
                >
                  <CheckCircle2 size={16} />
                  {busy === 'close' ? 'Calculating...' : 'Decide close'}
                </button>
              </section>
              <ScoreMetrics close={close} exceptions={exceptions} />
              <div class="dashboard-grid">
                <ExceptionTable
                  items={exceptions.slice(0, 6)}
                  onOpen={openException}
                  title="Recent exceptions"
                  subtitle="Latest items requiring attention"
                />
                <aside class="runs">
                  <RunPanel
                    title="Recent ingestion"
                    icon={Database}
                    items={ingestion}
                    action={ingest}
                    busy={busy === 'ingest'}
                  />
                  <RunPanel
                    title="Recent reconciliation"
                    icon={FileCheck2}
                    items={reconciliation}
                    action={reconcile}
                    busy={busy === 'reconcile'}
                  />
                </aside>
              </div>
            </>
          )}
          {view === 'exceptions' && (
            <>
              <section class="view-intro">
                <div>
                  <h1>Exception queue</h1>
                  <p>
                    Every unresolved amount has an owner, SLA, and audit trail.
                  </p>
                </div>
                <div class="search-filter">
                  <Search size={15} />
                  <select
                    value={status}
                    onChange={(e) => setStatus(e.currentTarget.value)}
                  >
                    <option value="">All statuses</option>
                    <option>OPEN</option>
                    <option>INVESTIGATING</option>
                    <option>PENDING_APPROVAL</option>
                    <option>REOPENED</option>
                    <option>RESOLVED</option>
                  </select>
                </div>
              </section>
              <section class="metrics compact">
                <Metric
                  label="Items in view"
                  value={exceptions.length}
                  detail="Filtered queue"
                />
                <Metric
                  label="Queue exposure"
                  value={money(
                    exceptions.reduce((sum, x) => sum + x.amount_paise, 0),
                  )}
                  detail="Gross unresolved value"
                />
                <Metric
                  label="Pending approval"
                  value={
                    exceptions.filter((x) => x.status === 'PENDING_APPROVAL')
                      .length
                  }
                  detail="Needs approver action"
                />
                <Metric
                  label="P1 items"
                  value={exceptions.filter((x) => x.priority === 'P1').length}
                  detail="Highest priority"
                  danger
                />
              </section>
              <ExceptionTable
                items={exceptions}
                onOpen={openException}
                title="All exceptions"
                subtitle={`${exceptions.length} records`}
              />
            </>
          )}
          {view === 'runs' && (
            <>
              <section class="view-intro">
                <div>
                  <h1>Control runs</h1>
                  <p>Execution history and reproducible control decisions.</p>
                </div>
                <div class="header-actions">
                  <button class="button" onClick={ingest}>
                    <Database size={15} />
                    Ingest
                  </button>
                  <button class="button primary" onClick={reconcile}>
                    <Play size={15} />
                    Reconcile
                  </button>
                </div>
              </section>
              <div class="run-view-grid">
                <RunPanel
                  title="Ingestion runs"
                  icon={Database}
                  items={ingestion}
                  action={ingest}
                  busy={busy === 'ingest'}
                  limit={50}
                />
                <RunPanel
                  title="Reconciliation runs"
                  icon={FileCheck2}
                  items={reconciliation}
                  action={reconcile}
                  busy={busy === 'reconcile'}
                  limit={50}
                />
              </div>
              <CloseHistory
                items={closes}
                onDecide={decideClose}
                busy={busy === 'close'}
              />
            </>
          )}
        </div>
      </main>
      {selected && (
        <div
          class="overlay"
          onClick={(e) => e.target === e.currentTarget && setSelected(null)}
        >
          <aside class="drawer">
            <div class="drawer-head">
              <div>
                <h3>Exception detail</h3>
                <span>{selected.exception_id}</span>
              </div>
              <button class="icon-button" onClick={() => setSelected(null)}>
                <X />
              </button>
            </div>
            <div class="drawer-body">
              <div class="exception-summary">
                <div>
                  <Badge value={selected.priority} />
                  <Badge value={selected.status} />
                </div>
                <h2>{selected.classification.replaceAll('_', ' ')}</h2>
                <strong>{money(selected.amount_paise)}</strong>
                <p>
                  {selected.partner_code} ·{' '}
                  {selected.assignee || selected.owner}
                </p>
              </div>
              <h3 class="section-title">Activity history</h3>
              <div class="timeline">
                {actions.map((action) => (
                  <div class="timeline-item">
                    <i />
                    <div>
                      <strong>{action.action.replaceAll('_', ' ')}</strong>
                      <span>{action.reason}</span>
                      <small>
                        {action.actor} · {action.actor_role}
                      </small>
                    </div>
                  </div>
                ))}
              </div>
            </div>
            <div class="drawer-actions">
              <button
                class="button"
                onClick={() => exceptionAction('investigate')}
              >
                Investigate
              </button>
              <button
                class="button"
                onClick={() => exceptionAction('request-resolution')}
              >
                Request resolution
              </button>
              <button
                class="button primary"
                onClick={() => exceptionAction('approve')}
              >
                Approve
              </button>
              <button
                class="button danger"
                onClick={() => exceptionAction('reject')}
              >
                Reject
              </button>
            </div>
          </aside>
        </div>
      )}
    </div>
  )
}

function Metric({ label, value, detail, danger }) {
  return (
    <article class="metric">
      <span>{label}</span>
      <strong class={danger ? 'red' : ''}>{value}</strong>
      <small>{detail}</small>
    </article>
  )
}
function ScoreMetrics({ close, exceptions }) {
  return (
    <section class="metrics">
      <Metric
        label="Matched value"
        value={close ? money(close.scorecard.matched_value_paise) : '--'}
        detail={
          close ? `${close.scorecard.matched_count} decisions` : 'No scorecard'
        }
      />
      <Metric
        label="Unresolved value"
        value={close ? money(close.scorecard.unresolved_value_paise) : '--'}
        detail={
          close
            ? `${close.scorecard.unresolved_count} decisions`
            : 'No scorecard'
        }
        danger={close?.scorecard.unresolved_value_paise > 0}
      />
      <Metric
        label="Pending value"
        value={close ? money(close.scorecard.pending_value_paise) : '--'}
        detail={
          close ? `${close.scorecard.pending_count} decisions` : 'No scorecard'
        }
      />
      <Metric
        label="Queue exposure"
        value={money(exceptions.reduce((sum, x) => sum + x.amount_paise, 0))}
        detail={`${exceptions.length} exceptions`}
      />
    </section>
  )
}
function ExceptionTable({ items, onOpen, title, subtitle }) {
  return (
    <section class="panel queue">
      <div class="panel-head">
        <div>
          <h3>{title}</h3>
          <span>{subtitle}</span>
        </div>
      </div>
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Priority</th>
              <th>Partner</th>
              <th>Classification</th>
              <th>Status</th>
              <th>Owner</th>
              <th class="right">Exposure</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr onClick={() => onOpen(item)}>
                <td>
                  <Badge value={item.priority} />
                </td>
                <td class="strong">{item.partner_code}</td>
                <td>{item.classification.replaceAll('_', ' ')}</td>
                <td>
                  <Badge value={item.status} />
                </td>
                <td>{item.assignee || item.owner}</td>
                <td class="right strong">{money(item.amount_paise)}</td>
              </tr>
            ))}
            {!items.length && (
              <tr>
                <td colspan="6" class="empty">
                  No exceptions in this view
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  )
}
function RunPanel({ title, icon: Icon, items, action, busy, limit = 4 }) {
  return (
    <section class="panel run-panel">
      <div class="panel-head">
        <div class="panel-title">
          <Icon size={17} />
          <h3>{title}</h3>
        </div>
        <button class="button small" onClick={action}>
          {busy ? 'Running...' : 'New run'}
        </button>
      </div>
      <div>
        {items.slice(0, limit).map((item) => (
          <div class="run-row">
            <div>
              <strong>{item.rule_version || item.status}</strong>
              <span>{shortId(item.run_key)}</span>
            </div>
            {item.status && <Badge value={item.status} />}
          </div>
        ))}
        {!items.length && <div class="empty">No runs yet</div>}
      </div>
    </section>
  )
}
function CloseHistory({ items, onDecide, busy }) {
  return (
    <section class="panel close-history">
      <div class="panel-head">
        <div>
          <h3>Close decisions</h3>
          <span>Persisted policy outcomes</span>
        </div>
        <button class="button small" onClick={onDecide}>
          {busy ? 'Calculating...' : 'Decide close'}
        </button>
      </div>
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Outcome</th>
              <th>Policy</th>
              <th>Actor</th>
              <th>Decision hash</th>
              <th class="right">Unresolved</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr>
                <td>
                  <Badge value={item.outcome} />
                </td>
                <td>{item.policy_version}</td>
                <td>{item.actor}</td>
                <td class="mono">{shortId(item.decision_hash)}</td>
                <td class="right strong">
                  {money(item.scorecard.unresolved_value_paise)}
                </td>
              </tr>
            ))}
            {!items.length && (
              <tr>
                <td colspan="5" class="empty">
                  No close decisions yet
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  )
}

render(<App />, document.getElementById('app'))
