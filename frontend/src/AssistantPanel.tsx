import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { api, ApiError } from './api';
import { publicationTime } from './model';
import type { AssistantAnswer, AssistantStatus, Draft, Publication } from './types';

const examples = ['¿Cuál es el importe extendido de mayo de 2016?', '¿Qué productos tienen mayor importe extendido?', '¿Cuál es el margen de utilidad?'];

export default function AssistantPanel({ token, publication, draft, unauthorized, refreshDashboard }: {
  token: string; publication: Publication; draft: Draft; unauthorized: () => void; refreshDashboard: () => void;
}) {
  const [status, setStatus] = useState<AssistantStatus | null>(null);
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState<AssistantAnswer | null>(null);
  const [asked, setAsked] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [context, setContext] = useState(true);
  const [revision, setRevision] = useState(0);
  const request = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setStatus(null); setError(null); setAnswer(null);
    request.current?.abort(); setBusy(false);
    void api.assistantStatus(token, controller.signal).then(value => { if (!controller.signal.aborted) setStatus(value); })
      .catch(failure => { if (!controller.signal.aborted) handleError(failure); });
    return () => { controller.abort(); request.current?.abort(); };
  }, [token, publication.id, revision]);
  function handleError(failure: unknown) {
    if (failure instanceof ApiError && failure.status === 401) unauthorized();
    else setError(failure instanceof ApiError ? failure : new ApiError('No fue posible consultar al asistente.', 503, 'AI_ERROR'));
  }
  async function ask(event: FormEvent) {
    event.preventDefault();
    if (busy || status?.status !== 'READY') return;
    request.current?.abort(); const controller = new AbortController(); request.current = controller;
    setBusy(true); setError(null); setAnswer(null); setAsked(question.trim());
    try {
      const value = await api.ask(token, question.trim(), draft, publication.id, controller.signal, context);
      if (!controller.signal.aborted) setAnswer(value);
    } catch (failure) { if (!controller.signal.aborted) handleError(failure); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  }
  return <section className="panel assistant-panel" id="asistente" aria-busy={busy}>
    <div className="panel-heading"><div><h2>Asistente de ventas <span className="ai-badge">IA VERIFICABLE</span></h2><p>Preguntas sobre indicadores, clientes, productos y períodos del histórico</p></div><button className="secondary" disabled={busy} onClick={() => setRevision(value => value + 1)}>Revisar proveedor</button></div>
    {!status && !error && <p className="module-state" role="status">Comprobando configuración del proveedor…</p>}
    {status?.status === 'NOT_CONFIGURED' && <div className="module-pending"><strong>Proveedor de IA pendiente de configuración</strong><p>{status.message}</p><span>La aplicación conserva todos sus indicadores y pronósticos.</span></div>}
    {status?.status === 'READY' && <><div className="assistant-provider"><span className="live-dot" />{status.provider} · {status.model}<span>Configurado · la conectividad se verifica al preguntar</span></div>
      <form onSubmit={event => void ask(event)} className="assistant-form"><label htmlFor="sales-question">Tu pregunta</label><textarea id="sales-question" value={question} minLength={2} maxLength={1000} required onChange={event => setQuestion(event.target.value)} placeholder="Por ejemplo: ¿cuántas facturas distintas hubo en 2015?" rows={3} disabled={busy} /><div className="assistant-controls"><label><input type="checkbox" checked={context} onChange={event => setContext(event.target.checked)} disabled={busy} /> Usar los filtros de ventas aplicados</label><span>{question.length}/1000</span><button className="primary" type="submit" disabled={busy || question.trim().length < 2}>{busy ? 'Consultando evidencias…' : 'Preguntar'}</button></div></form>
      <div className="assistant-examples">{examples.map(text => <button key={text} disabled={busy} onClick={() => setQuestion(text)}>{text}</button>)}</div></>}
    {error && <div className="message error module-message" role="alert"><span>{error.message}</span>{error.status === 409 && <button className="text-button" onClick={refreshDashboard}>Actualizar panel</button>}</div>}
    {busy && <p className="module-state" role="status">El proveedor interpreta la pregunta y organiza datos del corte publicado…</p>}
    {answer && <article className="assistant-answer"><div className="assistant-question">{asked}</div>
      {answer.status !== 'ANSWERED' ? <div className="assistant-insufficient"><strong>{answer.status === 'NEEDS_CLARIFICATION' ? 'Necesito precisar la consulta' : 'Información insuficiente'}</strong><p>{answer.message}</p></div> : <>
        {answer.partial_coverage && <p className="assistant-coverage-warning">La cobertura es parcial: la respuesta usa únicamente las fechas disponibles, indicadas en cada evidencia.</p>}
        {answer.paragraphs?.map((text, index) => <p className="assistant-paragraph" key={index}>{text} <a href={'#evidence-' + answer.citations?.[index]}>[{answer.citations?.[index]}]</a></p>)}
        {answer.effective_filters && <p className="assistant-filter-scope">Filtros verificados: {[
          answer.effective_filters.customer_id != null ? `comprador #${answer.effective_filters.customer_id}` : null,
          answer.effective_filters.bill_to_customer_id != null ? `facturado a #${answer.effective_filters.bill_to_customer_id}` : null,
          answer.effective_filters.stock_item_id != null ? `producto #${answer.effective_filters.stock_item_id}` : null,
        ].filter(Boolean).join(' · ') || 'todos los clientes y productos'} · {answer.effective_filters.is_credit_note == null ? 'todos los documentos' : answer.effective_filters.is_credit_note ? 'notas de crédito' : 'sin notas de crédito'}</p>}
        <details className="assistant-evidence"><summary>Ver evidencias y alcance de la respuesta</summary>{answer.evidence?.map(item => <div id={'evidence-' + item.id} key={item.id}><strong>{item.id} · {item.source}</strong><p>{item.statement}</p><code>{item.query_id}</code></div>)}<p>{answer.composition}</p></details>
        <div className="assistant-source"><span>Modelo: {answer.model}</span>{answer.publication && <span title={answer.publication.id}>Corte {answer.publication.id.slice(0, 8)} · {publicationTime(answer.publication.published_at_utc)}</span>}</div></>}
    </article>}
    <p className="module-footnote">Se consultan plantillas autorizadas del DW. Los importes conservan su unidad de origen; no se infieren causas, inventario actual ni cifras fuera de cobertura. Al preguntar, se envían la pregunta y evidencias agregadas al proveedor configurado.</p>
  </section>;
}
