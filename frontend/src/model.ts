import type { Coverage, Draft } from './types';

export const integer = (value: number) => new Intl.NumberFormat('es-HN').format(value);

// Redondeo de presentación con enteros; no convierte importes monetarios a float.
export function decimal(value: string | null, digits = 2): string {
  if (value === null) return '—';
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(value);
  if (!match) return '—';
  const fraction = (match[3] || '').padEnd(digits + 1, '0');
  let scaled = BigInt(match[2]) * 10n ** BigInt(digits) + BigInt(fraction.slice(0, digits) || '0');
  if (Number(fraction[digits]) >= 5) scaled += 1n;
  const parts = scaled.toString().padStart(digits + 1, '0');
  const whole = digits ? parts.slice(0, -digits) : parts;
  const result = new Intl.NumberFormat('es-HN').format(BigInt(whole));
  return (match[1] && scaled !== 0n ? '−' : '') + result + (digits ? '.' + parts.slice(-digits) : '');
}

export function nextDay(value: string): string {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || value.startsWith('0000')) throw new Error('Revisa las fechas seleccionadas.');
  const day = new Date(value + 'T00:00:00Z');
  if (!Number.isFinite(day.getTime()) || day.toISOString().slice(0, 10) !== value) throw new Error('Revisa las fechas seleccionadas.');
  day.setUTCDate(day.getUTCDate() + 1);
  const result = day.toISOString().slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(result)) throw new Error('La fecha hasta excede el intervalo admitido.');
  return result;
}

export function toQuery(draft: Draft, publicationId?: string): URLSearchParams {
  const result = new URLSearchParams();
  if (draft.from) { nextDay(draft.from); result.set('date_from', draft.from); }
  if (draft.until) result.set('date_to', nextDay(draft.until));
  if (draft.from && draft.until && draft.from > draft.until) throw new Error('Desde no puede ser posterior a hasta.');
  for (const value of [draft.customer, draft.product]) {
    if (value && (!/^-?\d+$/.test(value) || Number(value) < -(2 ** 31) || Number(value) >= 2 ** 31)) throw new Error('Revisa el cliente o producto seleccionado.');
  }
  if (draft.customer !== '') result.set(draft.role === 'buyer' ? 'customer_id' : 'bill_to_customer_id', draft.customer);
  if (draft.product !== '') result.set('stock_item_id', draft.product);
  if (draft.credit !== 'all') result.set('is_credit_note', draft.credit === 'credits' ? 'true' : 'false');
  result.set('customer_role', draft.role);
  if (publicationId) result.set('publication_id', publicationId);
  return result;
}

export function defaultDraft(coverage: Coverage): Draft {
  return { from: coverage.date_from || '', until: coverage.date_to || '', customer: '', product: '', role: 'buyer', credit: 'all' };
}

export const monthNames = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];
export function periodLabel(period: string) {
  return `${monthNames[Number(period.slice(5, 7)) - 1]} ${period.slice(2, 4)}`;
}
export function monthDraft(draft: Draft, period: string): Draft {
  const [year, month] = period.split('-').map(Number);
  const last = new Date(Date.UTC(year, month, 0)).toISOString().slice(0, 10);
  return { ...draft, from: period + '-01', until: last };
}
export function recentDraft(coverage: Coverage, draft: Draft): Draft {
  if (!coverage.date_to) return draft;
  const [year, month] = coverage.date_to.split('-').map(Number);
  const from = new Date(Date.UTC(year, month - 12, 1)).toISOString().slice(0, 10);
  return { ...draft, from: coverage.date_from && coverage.date_from > from ? coverage.date_from : from, until: coverage.date_to };
}
export function displayDate(value: string) {
  return new Intl.DateTimeFormat('es-HN', { year: 'numeric', month: 'short', day: '2-digit', timeZone: 'UTC' }).format(new Date(value + 'T00:00:00Z'));
}
export function publicationTime(value: string) {
  return new Intl.DateTimeFormat('es-HN', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC' }).format(new Date(value)) + ' UTC';
}
