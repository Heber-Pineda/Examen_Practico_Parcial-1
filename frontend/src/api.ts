import type { AssistantAnswer, AssistantStatus, Catalogs, Details, Draft, ForecastResult, NotificationsResult, Overview } from './types';
import { toQuery } from './model';

export class ApiError extends Error {
  constructor(message: string, public status: number, public code: string) { super(message); }
}

async function get<T>(path: string, token: string, signal: AbortSignal, payload?: object, timeout = 45000): Promise<T> {
  const controller = new AbortController();
  let timedOut = false;
  const abort = () => controller.abort();
  signal.addEventListener('abort', abort, { once: true });
  if (signal.aborted) controller.abort();
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeout);
  try {
    const response = await fetch('/api/v1/' + path, {
      headers: { Authorization: 'Bearer ' + token, Accept: 'application/json', ...(payload ? { 'Content-Type': 'application/json' } : {}) },
      method: payload ? 'POST' : 'GET', body: payload ? JSON.stringify(payload) : undefined,
      signal: controller.signal, cache: 'no-store',
    });
    const body = await response.json().catch(() => null);
    if (!response.ok) throw new ApiError(body?.error?.message || 'No fue posible consultar la API.', response.status, body?.error?.code || 'API_ERROR');
    if (!body) throw new ApiError('La API devolvió una respuesta no válida.', 502, 'INVALID_RESPONSE');
    return body as T;
  } catch (error) {
    if (timedOut) throw new ApiError('La consulta tardó demasiado. Intenta actualizar el panel.', 503, 'TIMEOUT');
    if (error instanceof ApiError || signal.aborted) throw error;
    throw new ApiError('No fue posible contactar con el servicio de analítica. Comprueba que la API esté iniciada.', 503, 'NETWORK');
  } finally {
    clearTimeout(timer);
    signal.removeEventListener('abort', abort);
  }
}

export const api = {
  assistantStatus: (token: string, signal: AbortSignal) => get<AssistantStatus>('assistant/status', token, signal),
  ask: (token: string, question: string, draft: Draft, publication: string, signal: AbortSignal, useContext = true, timeoutSeconds = 90) => {
    const context = useContext ? Object.fromEntries(toQuery(draft, publication)) : { publication_id: publication };
    const timeout = Number.isFinite(timeoutSeconds) ? Math.max(90, Math.min(300, timeoutSeconds)) * 1000 : 90000;
    return get<AssistantAnswer>('assistant/ask', token, signal, { ...context, question }, timeout);
  },
  notifications: (token: string, signal: AbortSignal) => get<NotificationsResult>('notifications', token, signal),
  readNotification: (token: string, id: string, signal: AbortSignal) =>
    get<{ status: 'READ' }>('notifications/' + encodeURIComponent(id) + '/read', token, signal, {}),
  predictions: (token: string, publication: string, signal: AbortSignal) =>
    get<ForecastResult>('ml/predictions?' + new URLSearchParams({ publication_id: publication }), token, signal),
  catalogs: (token: string, signal: AbortSignal) => get<Catalogs>('catalogs', token, signal),
  overview: (token: string, draft: Draft, publication: string, signal: AbortSignal) =>
    get<Overview>('overview?' + toQuery(draft, publication), token, signal),
  details: (token: string, draft: Draft, publication: string, page: number, size: number, sort: string, signal: AbortSignal) => {
    const query = toQuery(draft, publication);
    query.delete('customer_role');
    query.set('page', String(page)); query.set('page_size', String(size)); query.set('sort', sort);
    return get<Details>('sales?' + query, token, signal);
  },
};
