import { useEffect, useRef, useState } from 'react';
import { api, ApiError } from './api';
import { publicationTime } from './model';
import type { NotificationsResult } from './types';

export default function NotificationsPanel({ token, unauthorized }: { token: string; unauthorized: () => void }) {
  const [result, setResult] = useState<NotificationsResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [reading, setReading] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const acknowledge = useRef<AbortController | null>(null);
  useEffect(() => {
    setReading(null);
    let stopped = false;
    let pending: AbortController | null = null;
    async function load() {
      if (stopped || pending) return;
      const controller = new AbortController(); pending = controller; setBusy(true);
      try {
        const value = await api.notifications(token, controller.signal);
        if (!stopped) { setResult(value); setError(null); }
      } catch (failure) {
        if (stopped || controller.signal.aborted) return;
        if (failure instanceof ApiError && failure.status === 401) unauthorized();
        else setError(failure instanceof Error ? failure.message : 'No se pudo consultar el buzón.');
      } finally { pending = null; if (!stopped) setBusy(false); }
    }
    void load();
    const timer = setInterval(() => { if (document.visibilityState === 'visible') void load(); }, 30000);
    return () => { stopped = true; pending?.abort(); acknowledge.current?.abort(); clearInterval(timer); };
  }, [token, revision]);
  async function markRead(id: string) {
    acknowledge.current?.abort(); const controller = new AbortController(); acknowledge.current = controller;
    setReading(id);
    try { await api.readNotification(token, id, controller.signal); if (!controller.signal.aborted) setRevision(value => value + 1); }
    catch (failure) { if (!controller.signal.aborted) { if (failure instanceof ApiError && failure.status === 401) unauthorized(); else setError(failure instanceof Error ? failure.message : 'No fue posible registrar la lectura.'); } }
    finally { if (!controller.signal.aborted) setReading(null); }
  }
  return <section className="panel notifications-panel" id="notificaciones" aria-busy={busy}><div className="panel-heading"><div><h2>Notificaciones <span className="inbox-count">{result?.unread_count || 0}</span></h2><p>Buzón académico compartido · consulta automática cada 30 segundos</p></div><button className="secondary" disabled={busy} onClick={() => setRevision(value => value + 1)}>{busy ? 'Actualizando…' : 'Actualizar buzón'}</button></div>
    {error && <div role="alert" className="message error module-message">{error}</div>}
    {result?.status === 'NOT_READY' && <div className="module-pending"><strong>Buzón pendiente de preparación</strong><p>{result.message}</p></div>}
    {!result && !error && <p role="status" className="module-state">Consultando notificaciones…</p>}
    {result?.status === 'READY' && !result.items.length && <div className="inbox-empty"><span>✓</span><h3>No hay notificaciones entregadas</h3><p>El proceso de alertas evalúa y entrega fuera de la API. Ejecuta la demostración para comprobar el recorrido completo.</p></div>}
    <div className="notification-list">{result?.items.map(item => <article className={'notification-item ' + (item.read_at_utc ? 'was-read' : '')} key={item.notification_id}><div className="notification-mark">!</div><div className="notification-content"><div className="notification-heading"><h3>{item.title}</h3><span>{item.read_at_utc ? 'Leída' : 'Nueva'}</span></div><p>{item.body}</p><div className="notification-meta"><span>{publicationTime(item.delivered_at_utc)}</span><span>Período {item.period}</span><span title={item.source_publication}>Corte {item.source_publication.slice(0, 8)}</span><span>{item.delivery_status === 'DELIVERED' ? 'Entregada al buzón' : item.delivery_status}</span></div>{!item.read_at_utc && <button className="text-button" disabled={reading !== null} onClick={() => void markRead(item.notification_id)}>{reading === item.notification_id ? 'Registrando lectura…' : 'Marcar como leída'}</button>}</div></article>)}</div>
    <p className="module-footnote">Regla DEMO_SALES_MOM: variación del importe extendido frente al mes anterior. El umbral es configurable; las ejecuciones forzadas de demostración quedan identificadas. No representa una política comercial aprobada.</p></section>;
}
