import { useEffect, useRef } from 'react';
import * as echarts from 'echarts/core';
import { BarChart, LineChart } from 'echarts/charts';
import { AriaComponent, GridComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { EChartsCoreOption } from 'echarts/core';
import { decimal, periodLabel } from './model';
import type { ForecastModel, Overview, Ranking } from './types';

echarts.use([BarChart, LineChart, AriaComponent, GridComponent, TooltipComponent, CanvasRenderer]);

type Point = { value: number | null; raw: string | null; period?: string; id?: number; label: string };
type Tip = { name: string; data: Point };
const compact = (value: number) => new Intl.NumberFormat('es-HN', { notation: 'compact', maximumFractionDigits: 1 }).format(value);
const tooltip = { trigger: 'axis', renderMode: 'richText', backgroundColor: '#14233b', borderWidth: 0,
  textStyle: { color: '#fff', fontSize: 12 }, padding: [12, 16],
  formatter: (items: Tip | Tip[]) => {
    const item = Array.isArray(items) ? items[0] : items;
    return item ? item.data.label + '\n' + (item.data.raw === null ? 'Sin cobertura' : decimal(item.data.raw) + ' u.m.') : '';
  } };

function Canvas({ option, label, select }: { option: EChartsCoreOption; label: string; select?: (point: Point) => void }) {
  const element = useRef<HTMLDivElement>(null);
  const instance = useRef<echarts.EChartsType | null>(null);
  const callback = useRef(select); callback.current = select;
  useEffect(() => {
    if (!element.current) return;
    const chart = echarts.init(element.current, undefined, { renderer: 'canvas' });
    instance.current = chart;
    chart.on('click', params => { if (callback.current) callback.current(params.data as Point); });
    const observer = new ResizeObserver(() => chart.resize()); observer.observe(element.current);
    return () => { observer.disconnect(); chart.dispose(); instance.current = null; };
  }, []);
  useEffect(() => { instance.current?.setOption(option, { notMerge: true }); }, [option]);
  return <div ref={element} className="chart-canvas" role="img" aria-label={label} />;
}

export function TrendChart({ data, select }: { data: Overview; select: (period: string) => void }) {
  const points: Point[] = data.monthly.map(row => ({ value: row.kpis ? Number(row.kpis.extended_amount) : null,
    raw: row.kpis?.extended_amount ?? null, period: row.period, label: periodLabel(row.period) }));
  const option: EChartsCoreOption = { aria: { enabled: true }, animationDuration: 400,
    grid: { left: 18, right: 16, top: 24, bottom: 8, containLabel: true }, tooltip,
    xAxis: { type: 'category', boundaryGap: false, data: points.map(p => p.label),
      axisLine: { lineStyle: { color: '#e9edf5' } }, axisTick: { show: false }, axisLabel: { color: '#7b889d', fontSize: 10, hideOverlap: true } },
    yAxis: { type: 'value', splitNumber: 4, axisLabel: { color: '#7b889d', fontSize: 11, formatter: compact },
      splitLine: { lineStyle: { color: '#edf0f6', type: 'dashed' } } },
    series: [{ type: 'line', name: 'Importe extendido', smooth: false, connectNulls: false, symbol: 'circle', symbolSize: 5,
      showSymbol: points.length < 15, data: points, lineStyle: { color: '#3268ee', width: 3 }, itemStyle: { color: '#3268ee' },
      areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: 'rgba(50,104,238,.19)' }, { offset: 1, color: 'rgba(50,104,238,0)' }]) } }],
  };
  return <Canvas option={option} label="Tendencia mensual del importe extendido; pulsa un mes para filtrar" select={p => p.period && select(p.period)} />;
}

export function AnnualChart({ data, comparable }: { data: Overview; comparable: boolean }) {
  const useCommon = comparable && data.comparison_months.length > 0;
  const points = data.annual.map(row => {
    const metric = useCommon ? row.comparable_kpis : row.kpis;
    return { value: Number(metric?.extended_amount ?? 0), raw: metric?.extended_amount ?? '0.00',
      label: `${row.year} · ${useCommon ? data.comparison_months.length : row.observed_months.length} meses`,
      itemStyle: { color: row.observed_months.length < 12 ? '#2cb8a5' : '#7496f5', borderRadius: [6, 6, 0, 0] } };
  });
  const option: EChartsCoreOption = { aria: { enabled: true }, animationDuration: 400, tooltip,
    grid: { left: 8, right: 12, top: 24, bottom: 8, containLabel: true },
    xAxis: { type: 'category', data: data.annual.map(r => String(r.year)), axisLine: { lineStyle: { color: '#e9edf5' } },
      axisTick: { show: false }, axisLabel: { color: '#7b889d', fontSize: 11 } },
    yAxis: { type: 'value', splitNumber: 4, axisLabel: { color: '#7b889d', fontSize: 10, formatter: compact }, splitLine: { lineStyle: { color: '#edf0f6', type: 'dashed' } } },
    series: [{ type: 'bar', barMaxWidth: 42, data: points }],
  };
  return <Canvas option={option} label={useCommon ? 'Comparación anual de meses equivalentes' : 'Totales anuales de los períodos seleccionados'} />;
}

export function RankingChart({ rows, select, label }: { rows: Ranking[]; select: (id: number) => void; label: string }) {
  if (!rows.length) return <div className="chart-empty">No hay registros para esta selección.</div>;
  const option: EChartsCoreOption = { aria: { enabled: true }, animationDuration: 350,
    tooltip: { ...tooltip, trigger: 'item' }, grid: { left: 10, right: 32, top: 12, bottom: 8, containLabel: true },
    xAxis: { type: 'value', axisLabel: { color: '#7b889d', fontSize: 10, formatter: compact }, splitLine: { lineStyle: { color: '#edf0f6', type: 'dashed' } } },
    yAxis: { type: 'category', inverse: true, data: rows.map(r => r.name), axisTick: { show: false }, axisLine: { show: false },
      axisLabel: { color: '#596b84', width: 148, overflow: 'truncate', fontSize: 11 } },
    series: [{ type: 'bar', barMaxWidth: 13,
      data: rows.map((r, i) => ({ value: Number(r.kpis.extended_amount), raw: r.kpis.extended_amount, id: r.id, label: r.name,
        itemStyle: { color: i === 0 ? '#3268ee' : '#c2d1fb', borderRadius: [0, 4, 4, 0] } })) }],
  };
  return <Canvas option={option} label={label} select={point => { if (typeof point.id === 'number') select(point.id); }} />;
}

export function ForecastChart({ model }: { model: ForecastModel }) {
  const periods = [...model.history.map(row => row.period), ...model.forecast.map(row => row.period)];
  const historical = model.history.map(row => ({ value: Number(row.amount), raw: row.amount, label: periodLabel(row.period) }));
  const estimated = model.forecast.map(row => ({ value: Number(row.amount), raw: row.amount, label: periodLabel(row.period) }));
  const option: EChartsCoreOption = { aria: { enabled: true }, animationDuration: 350,
    grid: { left: 22, right: 30, top: 20, bottom: 12, containLabel: true },
    tooltip: { ...tooltip, formatter: (items: (Tip & { seriesName: string })[]) => items
      .filter(item => item.data && item.data.raw !== null)
      .map(item => `${item.data.label} · ${item.seriesName}\n${decimal(item.data.raw)} u.m.`).join('\n') },
    xAxis: { type: 'category', boundaryGap: false, data: periods.map(periodLabel), axisTick: { show: false },
      axisLine: { lineStyle: { color: '#e9edf5' } }, axisLabel: { color: '#7b889d', fontSize: 10, hideOverlap: true } },
    yAxis: { type: 'value', splitNumber: 4, axisLabel: { color: '#7b889d', fontSize: 10, formatter: compact },
      splitLine: { lineStyle: { color: '#edf0f6', type: 'dashed' } } },
    series: [{ name: 'Observado', type: 'line', symbolSize: 4, showSymbol: false, smooth: false,
      data: [...historical, ...estimated.map(() => null)], lineStyle: { color: '#3268ee', width: 2.5 }, itemStyle: { color: '#3268ee' } },
    { name: 'Estimado', type: 'line', symbolSize: 7, smooth: false,
      data: [...historical.slice(0, -1).map(() => null), { ...historical[historical.length - 1], raw: null }, ...estimated],
      lineStyle: { color: '#d69a35', width: 3, type: 'dashed' }, itemStyle: { color: '#d69a35' } }],
  };
  return <Canvas option={option} label="Histórico mensual observado en azul y tres estimaciones en ámbar discontinuo" />;
}
