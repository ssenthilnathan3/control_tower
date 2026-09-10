import { render } from 'preact'
import { useEffect, useState } from 'preact/hooks'
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Database,
  FileCheck2,
  LayoutDashboard,
  LoaderCircle,
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
  const [loading, setLoading] = useState('')
  const choose = async (token) => {
    try {
      setError('')
      setLoading(token)
      await onLogin(token)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading('')
    }
  }
  return (
    <div class="login">
      <div class="login-card">
        <div class="logo">CT</div>
        <h1>Control Tower</h1>
        <p>Choose a workspace role to continue.</p>
        <div class="role-options">
          <button
            class="role-option"
            disabled={Boolean(loading)}
            onClick={() => choose('operator-demo')}
          >
            <div class="role-icon">
              <Activity size={19} />
            </div>
            <div>
              <strong>Operations</strong>
              <span>Investigate and resolve exceptions</span>
            </div>
            {loading === 'operator-demo' && (
              <LoaderCircle class="spinner" size={17} />
            )}
          </button>
          <button
            class="role-option"
            disabled={Boolean(loading)}
            onClick={() => choose('approver-demo')}
          >
            <div class="role-icon approver">
              <ShieldCheck size={19} />
            </div>
            <div>
              <strong>Approver</strong>
              <span>Review approvals and decide close</span>
            </div>
            {loading === 'approver-demo' && (
              <LoaderCircle class="spinner" size={17} />
            )}
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
  const [exceptionSummary, setExceptionSummary] = useState([])
  const [exceptionPage, setExceptionPage] = useState({
    total: 0,
    limit: 25,
    offset: 0,
  })
  const [ingestion, setIngestion] = useState([])
  const [reconciliation, setReconciliation] = useState([])
  const [closes, setCloses] = useState([])
  const [selected, setSelected] = useState(null)
  const [actions, setActions] = useState([])
  const [actionReason, setActionReason] = useState('')
  const [actionError, setActionError] = useState('')
  const [selectedIds, setSelectedIds] = useState([])
  const [bulkReason, setBulkReason] = useState('')
  const [bulkMode, setBulkMode] = useState('')
  const [runOperation, setRunOperation] = useState('ingest')
  const [filters, setFilters] = useState({
    priority: '',
    partner: '',
    classification: '',
    status: '',
    owner: '',
    exposure: '',
  })
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState('')
  const [loading, setLoading] = useState(false)
  const [mobileNav, setMobileNav] = useState(false)
  const [view, setView] = useState(() => {
    const route = window.location.hash.slice(1)
    return ['overview', 'exceptions', 'runs'].includes(route)
      ? route
      : 'overview'
  })

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
    setLoading(true)
    try {
      const exceptionQuery = new URLSearchParams({
        limit: String(exceptionPage.limit),
        offset: String(exceptionPage.offset),
      })
      if (filters.priority) exceptionQuery.set('priority', filters.priority)
      if (filters.partner) exceptionQuery.set('partner_code', filters.partner)
      if (filters.classification)
        exceptionQuery.set(
          'classification',
          filters.classification.replaceAll(' ', '_'),
        )
      if (filters.status) exceptionQuery.set('status', filters.status)
      if (filters.owner) exceptionQuery.set('owner', filters.owner)
      if (filters.exposure)
        exceptionQuery.set(
          'min_amount_paise',
          String(Number(filters.exposure) * 100),
        )
      const [queue, summary, ingestions, reconciliations, decisions] =
        await Promise.all([
          api(`/api/exceptions?${exceptionQuery}`),
          api('/api/exception-summary'),
          api('/api/ingestion-runs'),
          api('/api/reconciliation-runs'),
          api('/api/close-decisions'),
        ])
      setExceptions(queue.items)
      setExceptionSummary(summary)
      setExceptionPage({
        total: queue.total,
        limit: queue.limit,
        offset: queue.offset,
      })
      setIngestion(ingestions.items)
      setReconciliation(reconciliations.items)
      setCloses(decisions.items)
      setNotice('')
    } catch (e) {
      setNotice(e.message)
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => {
    if (user) refresh()
  }, [user, exceptionPage.offset, filters])
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
  useEffect(() => {
    const syncRoute = () => {
      const route = window.location.hash.slice(1)
      if (['overview', 'exceptions', 'runs'].includes(route)) setView(route)
    }
    window.addEventListener('hashchange', syncRoute)
    if (!window.location.hash)
      window.history.replaceState(null, '', '#overview')
    return () => window.removeEventListener('hashchange', syncRoute)
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
      if (user.role !== 'APPROVER')
        throw new Error('Approver role is required to decide close.')
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
  const restart = () =>
    run('restart', () =>
      api('/api/restart', {
        method: 'POST',
        body: JSON.stringify({ directory: 'development' }),
      }),
    )
  const executeRunOperation = () => {
    const operations = { ingest, reconcile, close: decideClose, restart }
    operations[runOperation]()
  }
  const openException = async (item) => {
    setBusy('detail')
    try {
      setSelected(item)
      setActionReason('')
      setActionError('')
      setActions(await api(`/api/exceptions/${item.exception_id}/actions`))
    } catch (e) {
      setSelected(null)
      setNotice(e.message)
    } finally {
      setBusy('')
    }
  }
  const exceptionAction = async (action) => {
    if (!actionReason.trim()) {
      setActionError('Enter a reason before continuing.')
      return
    }
    setBusy(action)
    setActionError('')
    try {
      await api(`/api/exceptions/${selected.exception_id}/${action}`, {
        method: 'POST',
        body: JSON.stringify({ reason: actionReason.trim() }),
      })
      await refresh()
      setSelected(null)
    } catch (e) {
      setActionError(e.message)
    } finally {
      setBusy('')
    }
  }
  const resolveSelected = async () => {
    if (!bulkReason.trim()) {
      setNotice('Enter a bulk resolution reason before continuing.')
      return
    }
    setBusy('bulk-resolve')
    setNotice('')
    try {
      await api('/api/exceptions/resolve-selected', {
        method: 'POST',
        body: JSON.stringify({
          reason: bulkReason.trim(),
          exception_ids: selectedIds,
        }),
      })
      setSelectedIds([])
      setBulkReason('')
      setBulkMode('')
      await refresh()
    } catch (e) {
      setNotice(e.message)
      await refresh()
    } finally {
      setBusy('')
    }
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
  const filteredExceptions = exceptions.filter((item) => {
    const includes = (value, query) =>
      String(value || '')
        .toLowerCase()
        .includes(query.trim().toLowerCase())
    return (
      (!filters.priority || item.priority === filters.priority) &&
      includes(item.partner_code, filters.partner) &&
      includes(
        item.classification.replaceAll('_', ' '),
        filters.classification,
      ) &&
      (!filters.status || item.status === filters.status) &&
      includes(item.assignee || item.owner, filters.owner) &&
      (!filters.exposure || item.amount_paise >= Number(filters.exposure) * 100)
    )
  })
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
    window.location.hash = next
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
            Exceptions{' '}
            <em>
              {exceptionSummary.reduce((sum, item) => sum + item.count, 0)}
            </em>
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
            <button class="button" disabled={loading} onClick={refresh}>
              {loading ? (
                <LoaderCircle class="spinner" size={15} />
              ) : (
                <RefreshCw size={15} />
              )}
              {loading ? 'Loading...' : 'Refresh'}
            </button>
            {view !== 'exceptions' && (
              <button
                class="button primary"
                disabled={Boolean(busy)}
                onClick={reconcile}
              >
                {busy === 'reconcile' ? (
                  <LoaderCircle class="spinner" size={15} />
                ) : (
                  <Play size={15} />
                )}
                {busy === 'reconcile' ? 'Reconciling...' : 'Run reconciliation'}
              </button>
            )}
          </div>
        </header>
        {(loading || busy) && <div class="progress-line" />}
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
                  disabled={busy === 'close' || user.role !== 'APPROVER'}
                  title={
                    user.role !== 'APPROVER'
                      ? 'Approver role is required'
                      : undefined
                  }
                >
                  <CheckCircle2 size={16} />
                  {busy === 'close' ? 'Calculating...' : 'Decide close'}
                </button>
              </section>
              <ScoreMetrics close={close} exceptionSummary={exceptionSummary} />
              <div class="dashboard-grid">
                <ExceptionDonut items={exceptionSummary} />
                <aside class="runs">
                  <RunPanel
                    title="Recent ingestion"
                    icon={Database}
                    items={ingestion}
                  />
                  <RunPanel
                    title="Recent reconciliation"
                    icon={FileCheck2}
                    items={reconciliation}
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
                <button
                  class="button"
                  onClick={() =>
                    setFilters({
                      priority: '',
                      partner: '',
                      classification: '',
                      status: '',
                      owner: '',
                      exposure: '',
                    })
                  }
                >
                  Clear filters
                </button>
              </section>
              <section class="metrics compact">
                <Metric
                  label="Items in view"
                  value={filteredExceptions.length}
                  detail="Filtered queue"
                />
                <Metric
                  label="Queue exposure"
                  value={money(
                    filteredExceptions.reduce(
                      (sum, x) => sum + x.amount_paise,
                      0,
                    ),
                  )}
                  detail="Gross unresolved value"
                />
                <Metric
                  label="Pending approval"
                  value={
                    filteredExceptions.filter(
                      (x) => x.status === 'PENDING_APPROVAL',
                    ).length
                  }
                  detail="Needs approver action"
                />
                <Metric
                  label="P1 items"
                  value={
                    filteredExceptions.filter((x) => x.priority === 'P1').length
                  }
                  detail="Highest priority"
                  danger
                />
              </section>
              <div class="bulk-toolbar">
                <span>{selectedIds.length} selected</span>
                {user.role === 'APPROVER' && (
                  <>
                    <button
                      class="button primary"
                      disabled={!selectedIds.length || Boolean(busy)}
                      onClick={() => setBulkMode('selected')}
                    >
                      Resolve selected
                    </button>
                  </>
                )}
              </div>
              <ExceptionTable
                items={filteredExceptions}
                onOpen={openException}
                title="All exceptions"
                subtitle={`${filteredExceptions.length} of ${exceptions.length} records`}
                filters={filters}
                setFilters={setFilters}
                loading={busy === 'detail'}
                fixed
                page={exceptionPage}
                onPage={(offset) =>
                  setExceptionPage((current) => ({ ...current, offset }))
                }
                selectedIds={selectedIds}
                onSelection={setSelectedIds}
                canResolve={user.role === 'APPROVER'}
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
                  <select
                    class="operation-select"
                    value={runOperation}
                    disabled={Boolean(busy)}
                    onChange={(event) =>
                      setRunOperation(event.currentTarget.value)
                    }
                  >
                    <option value="ingest">Ingest source files</option>
                    <option value="reconcile">Run reconciliation</option>
                    <option value="close">Calculate close</option>
                    <option value="restart" disabled={user.role !== 'APPROVER'}>
                      Restart full pipeline
                    </option>
                  </select>
                  <button
                    class="button primary"
                    disabled={Boolean(busy)}
                    onClick={executeRunOperation}
                  >
                    {busy ? (
                      <LoaderCircle class="spinner" size={15} />
                    ) : (
                      <Play size={15} />
                    )}
                    {busy ? 'Running...' : 'Run'}
                  </button>
                </div>
              </section>
              <div class="run-view-grid">
                <RunPanel
                  title="Ingestion runs"
                  icon={Database}
                  items={ingestion}
                  limit={50}
                />
                <RunPanel
                  title="Reconciliation runs"
                  icon={FileCheck2}
                  items={reconciliation}
                  limit={50}
                />
              </div>
              <CloseHistory items={closes} />
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
                {busy === 'detail' && (
                  <div class="loading-inline">
                    <LoaderCircle class="spinner" size={18} />
                    Loading activity...
                  </div>
                )}
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
              <div class="action-form">
                <label for="action-reason">Action reason</label>
                <textarea
                  id="action-reason"
                  rows="3"
                  value={actionReason}
                  onInput={(e) => setActionReason(e.currentTarget.value)}
                  placeholder="Record evidence or rationale for this action"
                />
                {actionError && <div class="form-error">{actionError}</div>}
              </div>
            </div>
            <div class="drawer-actions">
              <button
                disabled={
                  Boolean(busy) ||
                  !['OPEN', 'REOPENED'].includes(selected.status)
                }
                class="button"
                onClick={() => exceptionAction('investigate')}
              >
                {busy === 'investigate' && (
                  <LoaderCircle class="spinner" size={14} />
                )}
                Investigate
              </button>
              <button
                disabled={Boolean(busy) || selected.status !== 'INVESTIGATING'}
                class="button"
                onClick={() => exceptionAction('request-resolution')}
              >
                {busy === 'request-resolution' && (
                  <LoaderCircle class="spinner" size={14} />
                )}
                Request resolution
              </button>
              <button
                disabled={
                  Boolean(busy) || selected.status !== 'PENDING_APPROVAL'
                }
                class="button primary"
                onClick={() => exceptionAction('approve')}
              >
                {busy === 'approve' && (
                  <LoaderCircle class="spinner" size={14} />
                )}
                Approve
              </button>
              <button
                disabled={
                  Boolean(busy) || selected.status !== 'PENDING_APPROVAL'
                }
                class="button danger"
                onClick={() => exceptionAction('reject')}
              >
                {busy === 'reject' && (
                  <LoaderCircle class="spinner" size={14} />
                )}
                Reject
              </button>
            </div>
          </aside>
        </div>
      )}
      {bulkMode && (
        <div class="modal-backdrop" onClick={() => !busy && setBulkMode('')}>
          <section
            class="bulk-modal"
            onClick={(event) => event.stopPropagation()}
          >
            <div class="modal-head">
              <div>
                <h2>Resolve selected anomalies</h2>
                <p>
                  {selectedIds.length} unresolved anomalies will be resolved.
                </p>
              </div>
              <button
                class="icon-button"
                disabled={Boolean(busy)}
                onClick={() => setBulkMode('')}
                aria-label="Close"
              >
                <X size={18} />
              </button>
            </div>
            <label for="bulk-reason">Resolution reason</label>
            <textarea
              id="bulk-reason"
              value={bulkReason}
              onInput={(event) => setBulkReason(event.currentTarget.value)}
              placeholder="Describe the evidence and decision"
              rows="4"
              autofocus
            />
            <div class="modal-actions">
              <button
                class="button"
                disabled={Boolean(busy)}
                onClick={() => setBulkMode('')}
              >
                Cancel
              </button>
              <button
                class="button primary"
                disabled={Boolean(busy) || !bulkReason.trim()}
                onClick={resolveSelected}
              >
                {busy && <LoaderCircle class="spinner" size={14} />}
                {busy ? 'Resolving...' : `Resolve ${selectedIds.length}`}
              </button>
            </div>
          </section>
        </div>
      )}
    </div>
  )
}

function ExceptionDonut({ items }) {
  const colors = ['#2563eb', '#f59e0b', '#ef4444', '#8b5cf6', '#14b8a6']
  const total = items.reduce((sum, item) => sum + item.count, 0)
  let cursor = 0
  const stops = items.map((item, index) => {
    const start = cursor
    cursor += total ? (item.count / total) * 100 : 0
    return `${colors[index % colors.length]} ${start}% ${cursor}%`
  })
  return (
    <section class="panel exception-chart">
      <div class="panel-head">
        <div>
          <h3>Exception distribution</h3>
          <span>All classifications by count</span>
        </div>
      </div>
      {total ? (
        <div class="chart-body">
          <div
            class="donut"
            style={{ background: `conic-gradient(${stops.join(',')})` }}
          >
            <div>
              <strong>{total}</strong>
              <span>exceptions</span>
            </div>
          </div>
          <div class="chart-legend">
            {items.map((item, index) => (
              <div>
                <i style={{ background: colors[index % colors.length] }} />
                <span>{item.classification.replaceAll('_', ' ')}</span>
                <strong>{item.count}</strong>
                <small>{money(item.amount_paise)}</small>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <div class="empty">No exceptions to chart</div>
      )}
    </section>
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
function ScoreMetrics({ close, exceptionSummary }) {
  const queueCount = exceptionSummary.reduce((sum, item) => sum + item.count, 0)
  const queueExposure = exceptionSummary.reduce(
    (sum, item) => sum + item.amount_paise,
    0,
  )
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
        value={money(queueExposure)}
        detail={`${queueCount} exceptions`}
      />
    </section>
  )
}
function ExceptionTable({
  items,
  onOpen,
  title,
  subtitle,
  filters,
  setFilters,
  loading,
  fixed,
  page,
  onPage,
  selectedIds,
  onSelection,
  canResolve,
}) {
  const update = (name, value) =>
    setFilters?.((current) => ({ ...current, [name]: value }))
  const eligible = items.filter(
    (item) => canResolve && item.status !== 'RESOLVED',
  )
  const allEligibleSelected =
    eligible.length > 0 &&
    eligible.every((item) => selectedIds?.includes(item.exception_id))
  const toggleAll = () => {
    if (allEligibleSelected) {
      onSelection(
        selectedIds.filter(
          (id) => !eligible.some((item) => item.exception_id === id),
        ),
      )
    } else {
      onSelection([
        ...new Set([
          ...(selectedIds || []),
          ...eligible.map((item) => item.exception_id),
        ]),
      ])
    }
  }
  const toggleOne = (id) =>
    onSelection(
      selectedIds.includes(id)
        ? selectedIds.filter((item) => item !== id)
        : [...selectedIds, id],
    )
  return (
    <section class="panel queue">
      <div class="panel-head">
        <div>
          <h3>{title}</h3>
          <span>{subtitle}</span>
        </div>
      </div>
      <div class={fixed ? 'table-scroll fixed' : 'table-scroll'}>
        <table>
          <thead>
            <tr>
              {onSelection && (
                <th class="check-cell">
                  <input
                    type="checkbox"
                    aria-label="Select all unresolved anomalies"
                    checked={allEligibleSelected}
                    disabled={!eligible.length}
                    onChange={toggleAll}
                  />
                </th>
              )}
              <th>Priority</th>
              <th>Partner</th>
              <th>Classification</th>
              <th>Status</th>
              <th>Owner</th>
              <th class="right">Exposure</th>
            </tr>
            {filters && (
              <tr class="filter-row">
                <th class="check-cell" />
                <th>
                  <select
                    value={filters.priority}
                    onChange={(e) => update('priority', e.currentTarget.value)}
                  >
                    <option value="">All</option>
                    <option>P1</option>
                    <option>P2</option>
                    <option>P3</option>
                  </select>
                </th>
                <th>
                  <input
                    value={filters.partner}
                    onInput={(e) => update('partner', e.currentTarget.value)}
                    placeholder="Filter partner"
                  />
                </th>
                <th>
                  <input
                    value={filters.classification}
                    onInput={(e) =>
                      update('classification', e.currentTarget.value)
                    }
                    placeholder="Filter class"
                  />
                </th>
                <th>
                  <select
                    value={filters.status}
                    onChange={(e) => update('status', e.currentTarget.value)}
                  >
                    <option value="">All</option>
                    <option>OPEN</option>
                    <option>INVESTIGATING</option>
                    <option>PENDING_APPROVAL</option>
                    <option>REOPENED</option>
                    <option>RESOLVED</option>
                  </select>
                </th>
                <th>
                  <input
                    value={filters.owner}
                    onInput={(e) => update('owner', e.currentTarget.value)}
                    placeholder="Filter owner"
                  />
                </th>
                <th>
                  <input
                    type="number"
                    min="0"
                    value={filters.exposure}
                    onInput={(e) => update('exposure', e.currentTarget.value)}
                    placeholder="Min INR"
                  />
                </th>
              </tr>
            )}
          </thead>
          <tbody>
            {loading && (
              <tr>
                <td colspan={onSelection ? 7 : 6} class="table-loader">
                  <LoaderCircle class="spinner" size={20} />
                  Loading details...
                </td>
              </tr>
            )}
            {items.map((item) => (
              <tr
                class={
                  selectedIds?.includes(item.exception_id) ? 'selected-row' : ''
                }
                onClick={() => onOpen(item)}
              >
                {onSelection && (
                  <td
                    class="check-cell"
                    onClick={(event) => event.stopPropagation()}
                  >
                    <input
                      type="checkbox"
                      aria-label={`Select ${item.exception_id}`}
                      checked={selectedIds.includes(item.exception_id)}
                      disabled={!canResolve || item.status === 'RESOLVED'}
                      onChange={() => toggleOne(item.exception_id)}
                    />
                  </td>
                )}
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
                <td colspan={onSelection ? 7 : 6} class="empty">
                  No exceptions in this view
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {page && (
        <div class="pagination">
          <span>
            {page.total
              ? `${page.offset + 1}-${Math.min(page.offset + page.limit, page.total)} of ${page.total}`
              : '0 records'}
          </span>
          <div>
            <button
              class="button small"
              disabled={loading || page.offset === 0}
              onClick={() => onPage(Math.max(0, page.offset - page.limit))}
            >
              Previous
            </button>
            <button
              class="button small"
              disabled={loading || page.offset + page.limit >= page.total}
              onClick={() => onPage(page.offset + page.limit)}
            >
              Next
            </button>
          </div>
        </div>
      )}
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
        {action && (
          <button class="button small" disabled={busy} onClick={action}>
            {busy && <LoaderCircle class="spinner" size={13} />}
            {busy ? 'Running...' : 'New run'}
          </button>
        )}
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
        {onDecide && (
          <button class="button small" disabled={busy} onClick={onDecide}>
            {busy && <LoaderCircle class="spinner" size={13} />}
            {busy ? 'Calculating...' : 'Decide close'}
          </button>
        )}
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
