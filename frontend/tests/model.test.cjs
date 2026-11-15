const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

// La salida aislada del compilador es CommonJS dentro de un proyecto ESM.
fs.writeFileSync(path.join(__dirname, '../.test-build/package.json'), '{"type":"commonjs"}\n');
const model = require('../.test-build/model.js');
const { api, ApiError } = require('../.test-build/api.js');
const draft = { from: '2016-05-01', until: '2016-05-31', customer: '', product: '', role: 'buyer', credit: 'all' };

test('importes exactos, signo, nulos y redondeo sin float monetario', () => {
  assert.equal(model.decimal('9007199254740993.01'), '9,007,199,254,740,993.01');
  assert.equal(model.decimal('-1234.995'), '−1,235.00');
  assert.equal(model.decimal('0.0049'), '0.00');
  assert.equal(model.decimal(null), '—');
  assert.equal(model.decimal('invalid'), '—');
});

test('fecha inclusiva de la interfaz se transforma en límite exclusivo y valida calendario', () => {
  assert.equal(model.toQuery(draft).get('date_to'), '2016-06-01');
  assert.equal(model.nextDay('2016-02-29'), '2016-03-01');
  assert.throws(() => model.nextDay('2016-02-30'));
  assert.throws(() => model.nextDay('0000-01-01'));
  assert.throws(() => model.nextDay('9999-12-31'));
  assert.throws(() => model.toQuery({ ...draft, until: '2016-04-30' }));
});

test('roles de cliente, ID cero, producto, créditos y publicación no se confunden', () => {
  const query = model.toQuery({ ...draft, customer: '0', product: '7', role: 'bill_to', credit: 'sales' }, 'publication-test');
  assert.equal(query.get('bill_to_customer_id'), '0');
  assert.equal(query.has('customer_id'), false);
  assert.equal(query.get('stock_item_id'), '7');
  assert.equal(query.get('is_credit_note'), 'false');
  assert.equal(query.get('publication_id'), 'publication-test');
  assert.throws(() => model.toQuery({ ...draft, product: '1; DROP TABLE x' }));
  assert.throws(() => model.toQuery({ ...draft, customer: '2147483648' }));
});

test('períodos rápidos usan el histórico disponible y conservan filtros al explorar un mes', () => {
  const coverage = { date_from: '2013-01-02', date_to: '2016-05-31', observed_months: [] };
  assert.equal(model.recentDraft(coverage, draft).from, '2015-06-01');
  assert.equal(model.recentDraft(coverage, draft).until, '2016-05-31');
  const selection = model.monthDraft({ ...draft, customer: '3' }, '2016-02');
  assert.equal(selection.until, '2016-02-29');
  assert.equal(selection.customer, '3');
  assert.equal(model.defaultDraft(coverage).from, '2013-01-02');
});

test('la clave viaja solo en Authorization y el detalle conserva filtros y paginación', async () => {
  const original = global.fetch;
  global.fetch = async (url, options) => {
    assert.equal(url.includes('isolated-test-token'), false);
    assert.equal(options.headers.Authorization, 'Bearer isolated-test-token');
    assert.equal(options.cache, 'no-store');
    const query = new URL(url, 'http://localhost').searchParams;
    assert.equal(query.get('stock_item_id'), '9');
    assert.equal(query.get('publication_id'), 'publication-test');
    assert.equal(query.get('page'), '2');
    assert.equal(query.get('page_size'), '25');
    assert.equal(query.has('customer_role'), false);
    return new Response(JSON.stringify({ total: 26 }), { status: 200 });
  };
  try {
    const result = await api.details('isolated-test-token', { ...draft, product: '9' }, 'publication-test', 2, 25, 'date_desc', new AbortController().signal);
    assert.equal(result.total, 26);
  } finally { global.fetch = original; }
});

test('un cambio de publicación se conserva como error recuperable para actualizar el panel', async () => {
  const original = global.fetch;
  global.fetch = async () => new Response(JSON.stringify({ error: { code: 'PUBLICATION_CHANGED', message: 'Actualiza el panel.' } }), { status: 409 });
  try {
    await assert.rejects(api.catalogs('isolated-test-token', new AbortController().signal), error =>
      error instanceof ApiError && error.status === 409 && error.code === 'PUBLICATION_CHANGED');
  } finally { global.fetch = original; }
});

test('predicciones consultan el artefacto del corte actual con autorización, sin filtros ni entrenamiento HTTP', async () => {
  const original = global.fetch;
  global.fetch = async (url, options) => {
    const target = new URL(url, 'http://localhost');
    assert.equal(target.pathname, '/api/v1/ml/predictions');
    assert.deepEqual([...target.searchParams.keys()], ['publication_id']);
    assert.equal(target.searchParams.get('publication_id'), '10bc721a-eeb1-4778-b494-8f520d2464cf');
    assert.equal(options.headers.Authorization, 'Bearer isolated-test-token');
    return new Response(JSON.stringify({ status: 'NOT_TRAINED', message: 'Isolated test' }), { status: 200 });
  };
  try {
    const result = await api.predictions('isolated-test-token', '10bc721a-eeb1-4778-b494-8f520d2464cf', new AbortController().signal);
    assert.equal(result.status, 'NOT_TRAINED');
  } finally { global.fetch = original; }
});

test('asistente envía una pregunta y filtros por POST sin exponer la clave en el cuerpo', async () => {
  const original = global.fetch;
  global.fetch = async (url, options) => {
    assert.equal(url, '/api/v1/assistant/ask');
    assert.equal(options.method, 'POST');
    assert.equal(options.headers['Content-Type'], 'application/json');
    assert.equal(options.headers.Authorization, 'Bearer isolated-test-token');
    const body = JSON.parse(options.body);
    assert.equal(body.question, 'Ventas de mayo');
    assert.equal(body.date_to, '2016-06-01');
    assert.equal(body.publication_id, 'publication-test');
    assert.equal(options.body.includes('isolated-test-token'), false);
    return new Response(JSON.stringify({ status: 'NOT_CONFIGURED' }), { status: 200 });
  };
  try {
    const response = await api.ask('isolated-test-token', 'Ventas de mayo', draft, 'publication-test', new AbortController().signal);
    assert.equal(response.status, 'NOT_CONFIGURED');
  } finally { global.fetch = original; }
});

test('lectura de notificación utiliza POST y la ruta de identificación explícita', async () => {
  const original = global.fetch;
  global.fetch = async (url, options) => {
    assert.equal(url, '/api/v1/notifications/10bc721a-eeb1-4778-b494-8f520d2464cf/read');
    assert.equal(options.method, 'POST');
    assert.deepEqual(JSON.parse(options.body), {});
    return new Response(JSON.stringify({ status: 'READ' }), { status: 200 });
  };
  try {
    assert.equal((await api.readNotification('isolated-test-token', '10bc721a-eeb1-4778-b494-8f520d2464cf', new AbortController().signal)).status, 'READ');
  } finally { global.fetch = original; }
});
