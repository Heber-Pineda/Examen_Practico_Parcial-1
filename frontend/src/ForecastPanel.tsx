import { useEffect, useState } from 'react';
import { api, ApiError } from './api';
import { ForecastChart } from './Chart';
import { decimal, publicationTime } from './model';
import type { ForecastMethod, ForecastResult, Publication } from './types';

const methodNames: Record<ForecastMethod, string> = { ridge: 'Regresión Ridge', seasonal_naive: 'Referencia estacional' };
const metric = (value: number) => new Intl.NumberFormat('es-HN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value);
const title = (period: string) => new Intl.DateTimeFormat('es-HN', { month: 'long', year: 'numeric', timeZone: 'UTC' }).format(new Date(period + '-01T00:00:00Z'));

export default function ForecastPanel({ token, publication, revision, unauthorized, refreshDashboard }: {
  token: string; publication: Publication; revision: number;
  unauthorized: () => void; refreshDashboard: () => void;
}) {
  const [result, setResult] = useState<ForecastResult | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setBusy(true); setError(null); setResult(null);
    void api.predictions(token, publication.id, controller.signal)
      .then(response => { if (!controller.signal.aborted) setResult(response); })
      .catch(failure => {
        if (controller.signal.aborted) return;
        if (failure instanceof ApiError && failure.status === 401) unauthorized();
        else setError(failure instanceof ApiError ? failure : new ApiError('No fue posible consultar las predicciones.', 503, 'ML_ERROR'));
      }).finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
    // Los filtros de ventas no entrenan ni filtran el modelo global.
  }, [token, publication.id, revision, retry]);

  const model = result && result.status !== 'NOT_TRAINED' ? result.model : null;
  return <section className="panel forecast-panel" id="predicciones" aria-busy={busy}>
    <div className="panel-heading"><div><h2>Ventas mensuales estimadas <span className="forecast-badge">PRONÓSTICO</span></h2><p>Importe extendido global · tres meses posteriores al cierre histórico · independiente de los filtros</p></div>
      <button className="secondary" disabled={busy} onClick={() => setRetry(value => value + 1)}>{busy ? 'Consultando…' : 'Actualizar pronóstico'}</button></div>
    {busy && <div className="forecast-state" role="status">Consultando el modelo publicado…</div>}
    {error && <div className="forecast-state"><div className="message error" role="alert"><span>{error.message}</span><button className="text-button" onClick={error.status === 409 ? refreshDashboard : () => setRetry(value => value + 1)}>{error.status === 409 ? 'Actualizar panel' : 'Reintentar'}</button></div></div>}
    {result?.status === 'NOT_TRAINED' && <div className="forecast-state forecast-untrained"><span className="forecast-state-icon">↗</span><h3>Modelo pendiente de entrenamiento</h3><p>{result.message}</p><p>Los indicadores y el detalle de ventas continúan disponibles.</p></div>}
    {model && <>
      {result?.status === 'STALE' && <div className="forecast-warning" role="alert">El DW tiene una publicación posterior a la utilizada por el modelo. Las estimaciones mostradas corresponden al corte anterior; vuelve a entrenar para actualizarlas.</div>}
      <div className="forecast-context"><span>Histórico hasta <strong>{title(model.trained_through)}</strong></span><span>Método seleccionado: <strong>{methodNames[model.selected_method]}</strong></span><span>{model.history.length} meses observados</span></div>
      <div className="forecast-legend"><span><i /> Histórico observado</span><span className="estimated"><i /> Estimación · {methodNames[model.selected_method]}</span></div>
      <ForecastChart model={model} />
      <div className="forecast-cards">{model.forecast.map(row => <article key={row.period}><span>{title(row.period)}</span><strong>{decimal(row.amount)} <small>u.m.</small></strong><span className="estimate-label">ESTIMACIÓN</span></article>)}</div>
      <p className="forecast-note">El horizonte parte del cierre del historial, no de la fecha actual. No hay intervalos de confianza; estas cifras son estimaciones. Se conserva el signo del importe y no se aplica un piso cero.</p>
      <div className="forecast-evaluation"><div className="forecast-splits"><h3>Evaluación cronológica</h3>{(['train', 'validation', 'test'] as const).map((split, i) => <p key={split}><strong>{['Entrenamiento', 'Validación', 'Prueba'][i]}</strong><span>{model.split[split].from} → {model.split[split].to} · {model.split[split].months} meses</span></p>)}<p className="forecast-evaluation-note">Cada bloque predice tres meses y usa únicamente meses anteriores. La validación elige el método y el parámetro; la prueba no interviene en esa elección. El ajuste final incorpora todo el historial.</p></div>
        <div className="table-wrap"><table><thead><tr><th>Evaluación</th><th>Método</th><th className="number">MAE · u.m.</th><th className="number">RMSE · u.m.</th></tr></thead><tbody>{(['validation', 'test'] as const).flatMap(split => (['ridge', 'seasonal_naive'] as const).map(method => <tr key={split + method}><td>{split === 'validation' ? 'Validación' : 'Prueba'}</td><td>{methodNames[method]}</td><td className="number">{metric(model.metrics[split][method].mae)}</td><td className="number">{metric(model.metrics[split][method].rmse)}</td></tr>))}</tbody></table><p className="forecast-note">Menor error es mejor. Si Ridge no mejora el MAE de validación, se utiliza la referencia estacional. Ridge α = {model.ridge_alpha}.</p></div></div>
      <div className="forecast-footer"><span>Versión <code title={model.model_version}>{model.model_version.slice(0, 8)}</code> · Entrenado {publicationTime(model.trained_at_utc)}</span><span title={model.source_publication.id}>Publicación de origen: {model.source_publication.id.slice(0, 8)}</span></div>
    </>}
  </section>;
}
