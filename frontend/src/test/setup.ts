import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach, vi } from 'vitest';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

class MockResizeObserver implements ResizeObserver {
  constructor(private callback: ResizeObserverCallback) {}
  observe(target: Element) { this.callback([{ target, contentRect: { width: 800, height: 400 } } as ResizeObserverEntry], this); }
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver = MockResizeObserver;
Object.defineProperty(HTMLElement.prototype, 'getBoundingClientRect', { value: () => ({ x: 0, y: 0, top: 0, left: 0, bottom: 400, right: 800, width: 800, height: 400, toJSON: () => ({}) }) });
HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); };

vi.mock('lightweight-charts', () => ({
  ColorType: { Solid: 'solid' }, CandlestickSeries: 'Candlestick', HistogramSeries: 'Histogram',
  createSeriesMarkers: vi.fn(() => ({ setMarkers: vi.fn() })),
  createChart: vi.fn(() => ({
    addSeries: vi.fn(() => ({ setData: vi.fn() })),
    panes: () => [{ setStretchFactor: vi.fn() }, { setStretchFactor: vi.fn() }],
    timeScale: () => ({ fitContent: vi.fn() }), resize: vi.fn(), remove: vi.fn(),
  })),
}));
