import { useEffect, useRef, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { api, ApiError } from './api';
import { AnnualChart, RankingChart, TrendChart } from './Chart';
import ForecastPanel from './ForecastPanel';
import AssistantPanel from './AssistantPanel';
import NotificationsPanel from './NotificationsPanel';
import { decimal, defaultDraft, displayDate, integer, monthDraft, monthNames, publicationTime, recentDraft, toQuery } from './model';
import type { Catalogs, Details, Draft, Overview } from './types';

const paths: Record<string, string> = {
  chart: 'M4 19V12m5 7V8m5 11V4m5 15V9M2 22h20',
  grid: 'M3 3h7v7H3zm11 0h7v7h-7zM3 14h7v7H3zm11 0h7v7h-7z',
  table: 'M3 4h18v16H3zM3 9h18M9 9v11',
  refresh: 'M20 7v5h-5M4 17v-5h5M5 7a8 8 0 0 1 13-2l2 3M4 16l2 3a8 8 0 0 0 13-2',
  clock: 'M12 8v5l3 2M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',
  shield: 'M12 3 3 7v6c0 5 9 9 9 9s9-4 9-9V7zM8 12l3 3 5-6',
  logout: 'M9 4H4v16h5m5-12 4 4-4 4m-5-4h13',
  arrow: 'm8 5 7 7-7 7',
  filter: 'M3 4h18l-7 8v7l-4 2v-9z',
  box: 'm3 7 9-4 9 4-9 5zm0 0v10l9 4 9-4V7M12 12v9',
  file: 'M6 3h9l4 4v14H6zM14 3v6h5M9 13h7m-7 4h7',
  coin: 'M12 5v14m4-11c-1-3-8-2-8 1 0 4 8 2 8 6 0 3-7 4-9 0M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',
  percent: 'M6 18 18 6M9 6a3 3 0 1 1-6 0 3 3 0 0 1 6 0m12 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0',
};
function Icon({ name, spin = false }: { name: string; spin?: boolean }) {
  return <svg className={spin ? 'icon spin' : 'icon'} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name] || paths.chart} /></svg>;
}
function Brand() { return <div className="brand"><span className="brand-mark"><i /><i /><i /></span><div>WWI<span>ANALÍTICA DE VENTAS</span></div></div>; }
function Message({ children, action, warning = false }: { children: ReactNode; action?: () => void; warning?: boolean }) {
  return <div role="alert" className={'message ' + (warning ? 'warning' : 'error')}><span>{children}</span>{action && <button className="text-button" onClick={action}>Actualizar panel <span aria-hidden="true">↗</span></button>}</div>;
}

function Access({ enter, error }: { enter: (key: string) => void; error: string | null }) {
  const [key, setKey] = useState('');
  return <main className="access-screen"><div className="access-art"><Brand /><div className="access-copy"><span className="eyebrow">DATOS QUE CONECTAN DECISIONES</span><h1>Una visión clara<br />de cada venta.</h1><p>Indicadores, tendencias y detalle comercial<br />en un mismo espacio de análisis.</p><div className="abstract-bars" aria-hidden="true">{[30, 46, 35, 60, 53, 76, 64, 88, 80, 100].map((v, i) => <i key={i} style={{ height: v + '%' }} />)}</div></div><span className="access-foot">Plataforma Integral de Ingeniería de Datos y Analítica Inteligente</span></div>
    <section className="access-panel"><div className="access-form"><span className="access-shield"><Icon name="shield" /></span><h2>Acceso al panel</h2><p>Ingresa la clave de acceso proporcionada para consultar la información de ventas.</p>{error && <Message>{error}</Message>}
      <form onSubmit={event => { event.preventDefault(); enter(key.trim()); }}><label className="field">Clave de acceso<input type="password" value={key} onChange={e => setKey(e.target.value)} autoComplete="off" required placeholder="Ingresa tu clave" /></label><button className="primary full" type="submit">Entrar al panel <Icon name="arrow" /></button></form><div className="access-note"><Icon name="shield" /> Consulta protegida · La clave no se guarda en el navegador.</div></div></section></main>;
}

function KpiCards({ data, busy }: { data: Overview | null; busy: boolean }) {
  const metrics = data?.kpis;
  const cards = [
    { label: 'Importe base', value: metrics ? decimal(metrics.base_amount) : '—', detail: metrics?.base_status === 'INCOMPLETO' ? 'Precio faltante · importe incompleto' : 'Sin impuestos añadidos', icon: 'coin', color: 'blue' },
    { label: 'Importe extendido', value: metrics ? decimal(metrics.extended_amount) : '—', detail: 'Importe registrado en las ventas', icon: 'chart', color: 'teal' },
    { label: 'Unidades vendidas', value: metrics ? integer(metrics.units) : '—', detail: 'Cantidad según el origen', icon: 'box', color: 'violet' },
    { label: 'Facturas distintas', value: metrics ? integer(metrics.invoice_count) : '—', detail: 'Con líneas en esta selección', icon: 'file', color: 'amber' },
    { label: 'Utilidad registrada', value: metrics ? decimal(metrics.profit_amount) : '—', detail: 'Utilidad almacenada en origen', icon: 'coin', color: 'teal' },
    { label: 'Margen de utilidad', value: metrics?.profit_margin != null ? decimal(metrics.profit_margin) + '%' : '—', detail: metrics?.margin_status === 'DENOMINADOR_CERO' ? 'Importe base igual a cero' : metrics?.margin_status === 'INCOMPLETO' ? 'Base de cálculo incompleta' : 'Utilidad / importe base', icon: 'percent', color: 'blue' },
  ];
  return <section className="kpis" aria-label="Indicadores de ventas" aria-busy={busy}>{cards.map(card => <article className="kpi-card" key={card.label}><div className="kpi-top"><span>{card.label}</span><span className={'kpi-icon ' + card.color}><Icon name={card.icon} /></span></div><div className={'kpi-value ' + (busy && !data ? 'skeleton' : '')}>{card.value}</div><div className="kpi-detail">{card.detail}</div></article>)}</section>;
}

export default function App() {
  const [token, setToken] = useState<string | null>(null);
  const [loginError, setLoginError] = useState<string | null>(null);
  const [catalogs, setCatalogs] = useState<Catalogs | null>(null);
  const [data, setData] = useState<Overview | null>(null);
  const [details, setDetails] = useState<Details | null>(null);
  const [draft, setDraft] = useState<Draft>({ from: '', until: '', customer: '', product: '', role: 'buyer', credit: 'all' });
  const [active, setActive] = useState<Draft | null>(null);
  const [busy, setBusy] = useState(false);
  const [tableBusy, setTableBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tableError, setTableError] = useState<string | null>(null);
  const [size, setSize] = useState(25);
  const [sort, setSort] = useState('date_desc');
  const [equivalent, setEquivalent] = useState(true);
  const [forecastRevision, setForecastRevision] = useState(0);
  const catalogRef = useRef<Catalogs | null>(null);
  const request = useRef(0);
  const pageRequest = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const pageController = useRef<AbortController | null>(null);
  useEffect(() => () => { controller.current?.abort(); pageController.current?.abort(); }, []);

  function logout() {
    ++request.current; ++pageRequest.current;
    controller.current?.abort(); pageController.current?.abort();
    catalogRef.current = null; setToken(null); setCatalogs(null); setData(null); setDetails(null);
    setActive(null); setBusy(false); setTableBusy(false); setError(null); setTableError(null);
  }
  async function load(access: string, next?: Draft, refresh = false) {
    const sequence = ++request.current;
    ++pageRequest.current; controller.current?.abort(); pageController.current?.abort();
    const current = new AbortController(); controller.current = current;
    setBusy(true); setError(null); setTableError(null); setTableBusy(false);
    try {
      const catalog = refresh || !catalogRef.current ? await api.catalogs(access, current.signal) : catalogRef.current;
      const selection = next || defaultDraft(catalog.coverage);
      toQuery(selection);
      const [overview, rows] = await Promise.all([
        api.overview(access, selection, catalog.publication.id, current.signal),
        api.details(access, selection, catalog.publication.id, 1, size, sort, current.signal),
      ]);
      if (sequence !== request.current) return;
      catalogRef.current = catalog; setCatalogs(catalog); setData(overview); setDetails(rows);
      setDraft(selection); setActive(selection);
      if (refresh) setForecastRevision(value => value + 1);
    } catch (failure) {
      if (current.signal.aborted || sequence !== request.current) return;
      current.abort();
      const message = failure instanceof Error ? failure.message : 'No fue posible cargar el panel.';
      if (failure instanceof ApiError && failure.status === 401) { logout(); setLoginError(message); }
      else setError(message);
    } finally { if (sequence === request.current) setBusy(false); }
  }
  async function loadPage(page: number, nextSize = size, nextSort = sort) {
    if (!token || !data || !active) return;
    const sequence = ++pageRequest.current;
    pageController.current?.abort(); const current = new AbortController(); pageController.current = current;
    setTableBusy(true); setTableError(null);
    try {
      const result = await api.details(token, active, data.publication.id, page, nextSize, nextSort, current.signal);
      if (sequence === pageRequest.current) { setDetails(result); setSize(nextSize); setSort(nextSort); }
    } catch (failure) {
      if (current.signal.aborted || sequence !== pageRequest.current) return;
      if (failure instanceof ApiError && failure.status === 401) { logout(); setLoginError(failure.message); }
      else setTableError(failure instanceof Error ? failure.message : 'No fue posible cargar el detalle.');
    } finally { if (sequence === pageRequest.current) setTableBusy(false); }
  }
  const refresh = () => { if (token) void load(token, active || undefined, true); };
  const apply = (selection: Draft) => { if (token && !busy) void load(token, selection); };
  const patchDraft = (patch: Partial<Draft>) => setDraft(previous => ({ ...previous, ...patch }));
  if (!token) return <Access error={loginError} enter={access => { setLoginError(null); setToken(access); void load(access, undefined, true); }} />;
  const haveSales = data !== null && data.kpis.line_count > 0;
  const coverage = catalogs?.coverage;

  return <div className="app-shell"><aside className="sidebar"><Brand /><div className="nav-caption">ESPACIO DE ANÁLISIS</div><nav aria-label="Navegación del panel"><a className="nav-item selected" href="#resumen"><Icon name="grid" /> Resumen de ventas <span className="nav-dot" /></a><a className="nav-item" href="#evolucion"><Icon name="chart" /> Evolución comercial</a><a className="nav-item" href="#detalle"><Icon name="table" /> Detalle de ventas</a><a className="nav-item" href="#predicciones"><Icon name="chart" /> Pronóstico mensual</a><a className="nav-item" href="#asistente"><Icon name="file" /> Asistente de ventas</a><a className="nav-item" href="#notificaciones"><Icon name="clock" /> Notificaciones</a></nav><div className="sidebar-source"><span className="source-icon"><Icon name="box" /></span><strong>WideWorldImporters</strong><span>Información comercial</span><div className="source-status"><i className={data ? 'connected' : ''} />{data ? 'Datos publicados' : 'Conectando al servicio'}</div></div><div className="sidebar-bottom"><span><Icon name="shield" /> Acceso de consulta</span><button onClick={logout}><Icon name="logout" /> Cerrar sesión</button></div></aside>
    <main className="workspace" id="resumen"><div className="topbar"><div className="breadcrumb">Analítica <span>/</span> <strong>Ventas</strong></div><div className="topbar-right"><span className="read-only"><Icon name="shield" /> Solo lectura</span><button className="mobile-logout" onClick={logout} aria-label="Cerrar sesión"><Icon name="logout" /></button><span className="avatar" aria-hidden="true">BI</span></div></div>
      <header className="page-header"><div><span className="eyebrow">VISIÓN GENERAL</span><h1>Panorama de ventas<span>.</span></h1><p>La información que necesitas para entender tu desempeño comercial.</p></div><button className="secondary refresh-button" onClick={refresh} disabled={busy}><Icon name="refresh" spin={busy} />{busy ? 'Actualizando' : 'Actualizar'}</button></header>
      <div className="publication-bar"><div><span className="live-dot" />{data ? 'Publicación exitosa' : 'Esperando datos publicados'}{data && <span className="publication-date"><Icon name="clock" />{publicationTime(data.publication.published_at_utc)}</span>}</div>{coverage && <span>{integer(coverage.observed_months.length)} meses con registros</span>}</div>
      {error && <Message action={refresh} warning={data !== null}>{error}{data && ' Se conserva la vista anterior; los cambios solicitados no se aplicaron.'}</Message>}
      <form className="filters-panel" onSubmit={(event: FormEvent) => { event.preventDefault(); apply(draft); }}><div className="filter-heading"><strong><Icon name="filter" /> Explorar ventas</strong><span>Selecciona un período y aplica tus filtros</span></div><div className="filters-grid"><label className="field">Desde<input aria-label="Fecha desde" type="date" value={draft.from} onChange={e => patchDraft({ from: e.target.value })} /></label><label className="field">Hasta <span className="field-note">inclusive</span><input aria-label="Fecha hasta" type="date" value={draft.until} onChange={e => patchDraft({ until: e.target.value })} /></label><label className="field"><span className="customer-label">Cliente<select aria-label="Rol de cliente" className="role-select" value={draft.role} onChange={e => patchDraft({ role: e.target.value as Draft['role'] })}><option value="buyer">Comprador</option><option value="bill_to">Facturado a</option></select></span><select aria-label="Cliente" value={draft.customer} onChange={e => patchDraft({ customer: e.target.value })}><option value="">Todos los clientes</option>{catalogs?.customers.map(row => <option key={row.id} value={String(row.id)}>{row.name} · {row.id}</option>)}</select></label><label className="field">Producto<select aria-label="Producto" value={draft.product} onChange={e => patchDraft({ product: e.target.value })}><option value="">Todos los productos</option>{catalogs?.products.map(row => <option key={row.id} value={String(row.id)}>{row.name}</option>)}</select></label><button type="submit" className="primary filter-apply" disabled={busy || !catalogs}>Aplicar filtros <Icon name="arrow" /></button></div><div className="filter-bottom"><div className="quick-periods"><button type="button" onClick={() => catalogs && apply(defaultDraft(catalogs.coverage))} disabled={busy || !catalogs}>Todo el histórico</button><button type="button" onClick={() => catalogs && apply(recentDraft(catalogs.coverage, draft))} disabled={busy || !catalogs}>Últimos 12 meses del histórico</button></div><label className="document-select">Documentos<select aria-label="Tipo de documento" value={draft.credit} onChange={e => patchDraft({ credit: e.target.value as Draft['credit'] })}><option value="all">Todos</option><option value="sales">Sin notas de crédito</option><option value="credits">Notas de crédito</option></select></label></div></form>
      {catalogs?.catalogs_truncated && <Message warning>El catálogo excede el límite de opciones del panel. Es necesario ampliar su búsqueda antes de usar todos los filtros.</Message>}
      <div className="section-intro"><h2>Indicadores clave</h2><span>{active?.from && active.until ? displayDate(active.from) + ' — ' + displayDate(active.until) : 'Todo el período disponible'}</span></div><KpiCards data={data} busy={busy} />
      {busy && !data ? <div className="loading-panel" role="status"><Icon name="refresh" spin /> Preparando indicadores y visualizaciones…</div> : data && !haveSales ? <section className="empty-panel"><span className="empty-icon"><Icon name="chart" /></span><h2>No hay ventas para esta selección</h2><p>Prueba otro período, cliente o producto. La ausencia de registros fuera del histórico no representa ventas cero.</p><button className="secondary" onClick={() => catalogs && apply(defaultDraft(catalogs.coverage))}>Restablecer filtros</button></section> : haveSales && data && <><section className="chart-grid" id="evolucion"><article className="panel trend-panel"><div className="panel-heading"><div><h2>Evolución mensual</h2><p>Importe extendido · pulsa un mes para explorar</p></div><span className="series-label"><i /> Ventas</span></div><TrendChart data={data} select={period => active && apply(monthDraft(active, period))} /></article><article className="panel"><div className="panel-heading"><div><h2>Comparación anual</h2><p>Importe extendido por año</p></div></div><div className="comparison-options"><label><input type="checkbox" checked={equivalent} onChange={e => setEquivalent(e.target.checked)} disabled={!data.comparison_months.length} /> Meses equivalentes</label><span>{equivalent && data.comparison_months.length ? data.comparison_months.map(m => monthNames[m - 1]).join(' · ') : 'Períodos seleccionados'}</span></div><AnnualChart data={data} comparable={equivalent} /><p className="chart-note">{equivalent && data.comparison_months.length ? 'Se comparan los meses comunes entre años.' : 'Los totales pueden cubrir distinta cantidad de meses.'}{data.comparison_partial_dates && ' Hay límites de fecha dentro de un mes.'}</p></article></section>
        <section className="ranking-grid"><article className="panel"><div className="panel-heading"><div><h2>Productos principales</h2><p>Mayor importe extendido · pulsa para filtrar</p></div><span className="tag">TOP {data.top_products.length}</span></div><RankingChart rows={data.top_products} label="Productos con mayor importe extendido" select={id => active && apply({ ...active, product: String(id) })} /></article><article className="panel"><div className="panel-heading"><div><h2>Clientes principales</h2><p>{data.customer_role === 'buyer' ? 'Compradores' : 'Responsables de facturación'} · mayor importe extendido</p></div><span className="tag">TOP {data.top_customers.length}</span></div><RankingChart rows={data.top_customers} label="Clientes con mayor importe extendido" select={id => active && apply({ ...active, customer: String(id) })} /></article></section></>}
      <section className="panel sales-panel" id="detalle" aria-busy={tableBusy || busy}><div className="panel-heading"><div><h2>Detalle de ventas</h2><p>{details ? integer(details.total) + ' líneas en la selección actual' : 'Ventas de la publicación seleccionada'}</p></div><label className="sort-label">Ordenar<select aria-label="Orden del detalle" value={sort} disabled={busy || tableBusy || !data} onChange={e => void loadPage(1, size, e.target.value)}><option value="date_desc">Más recientes</option><option value="amount_desc">Mayor importe</option></select></label></div>
        {tableError && <Message warning action={refresh}>{tableError} El detalle anterior sigue visible.</Message>}
        <div className={'table-wrap ' + (tableBusy ? 'table-loading' : '')}><table><thead><tr><th>Fecha / factura</th><th>Cliente</th><th>Producto</th><th className="number">Unidades</th><th className="number">Precio unitario</th><th className="number">Importe base</th><th className="number">Extendido</th><th className="number">Utilidad</th></tr></thead><tbody>{details?.items.map(row => <tr key={row.invoice_line_id}><td><strong>{displayDate(row.invoice_date)}</strong><span>#{row.invoice_id} · línea {row.invoice_line_id}{row.is_credit_note ? ' · Crédito' : ''}</span></td><td><strong>{row.customer_name || 'Sin etiqueta'}</strong><span>Facturado a {row.bill_to_customer_name || row.bill_to_customer_id}</span></td><td><strong>{row.product_name || 'Sin etiqueta'}</strong><span>Producto {row.stock_item_id}</span></td><td className="number">{integer(row.quantity)}</td><td className="number">{decimal(row.unit_price)}</td><td className="number">{decimal(row.base_amount)}</td><td className="number"><strong>{decimal(row.extended_amount)}</strong><span>Impuestos {decimal(row.tax_amount)}</span></td><td className="number profit">{decimal(row.profit_amount)}</td></tr>)}{!details?.items.length && <tr><td colSpan={8} className="table-empty">{busy || tableBusy ? 'Cargando detalle…' : 'No hay líneas de venta para mostrar.'}</td></tr>}</tbody></table></div>
        <div className="pagination"><label>Filas por página<select aria-label="Filas por página" value={size} disabled={busy || tableBusy || !data} onChange={e => void loadPage(1, Number(e.target.value))}>{[10, 25, 50, 100].map(n => <option key={n} value={n}>{n}</option>)}</select></label><div><span>{details && details.total ? `${integer((details.page - 1) * details.page_size + 1)}–${integer(Math.min(details.page * details.page_size, details.total))} de ${integer(details.total)}` : '0 registros'}</span><button className="page-button" aria-label="Página anterior" disabled={busy || tableBusy || !details || details.page <= 1} onClick={() => details && void loadPage(details.page - 1)}><span className="reverse"><Icon name="arrow" /></span></button><span className="page-indicator">{details?.total_pages ? details.page : 0} / {details?.total_pages || 0}</span><button className="page-button" aria-label="Página siguiente" disabled={busy || tableBusy || !details || details.page >= details.total_pages} onClick={() => details && void loadPage(details.page + 1)}><Icon name="arrow" /></button></div></div></section>
      {data && <ForecastPanel token={token} publication={data.publication} revision={forecastRevision} unauthorized={logout} refreshDashboard={refresh} />}
      {data && active && <div className="intelligence-grid"><AssistantPanel token={token} publication={data.publication} draft={active} unauthorized={logout} refreshDashboard={refresh} /><NotificationsPanel token={token} unauthorized={logout} /></div>}
      <footer className="page-footer"><span><span className="live-dot" /> Indicadores del Data Warehouse publicado</span><span>Importes en la unidad monetaria del origen · Moneda pendiente de confirmar</span></footer>
    </main></div>;
}
