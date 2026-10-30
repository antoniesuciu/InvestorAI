import { expect, test } from '@playwright/test';
import { syntheticCompanies, syntheticComparison, syntheticFinancials, syntheticMarket } from '../src/test/fixtures';

test.describe('explicit synthetic browser fixtures', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/api/**', async route => {
      const url = new URL(route.request().url());
      const body = url.pathname === '/api/companies' ? { companies: syntheticCompanies, data_kind: 'synthetic' }
        : url.pathname === '/api/compare' ? syntheticComparison
        : url.pathname.endsWith('/financials') ? syntheticFinancials : syntheticMarket;
      await route.fulfill({ json: body });
    });
  });

  test('renders real chart libraries, filters, all views, and keyboard evidence navigation', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('/');
    await expect(page.getByText('Synthetic test data')).toBeVisible();
    await expect(page.getByTestId('price-chart').locator('canvas').first()).toBeVisible();
    const margin = page.getByRole('button', { name: /Net margin: 15%/ });
    await margin.focus();
    await page.keyboard.press('Enter');
    const dialog = page.getByRole('dialog', { name: 'Evidence & provenance' });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText('net_income / revenue * 100')).toBeVisible();
    await expect(dialog.getByRole('link', { name: 'Original SEC filing' }).first()).toHaveAttribute('href', /^https:\/\/www.sec.gov\//);
    await page.keyboard.press('Escape');
    await expect(dialog).not.toBeVisible();
    await expect(margin).toBeFocused();
    await page.getByLabel('Company', { exact: true }).selectOption('MSFT');
    await expect(page.getByLabel('Company', { exact: true })).toHaveValue('MSFT');
    await page.getByRole('button', { name: 'Financials', exact: true }).click();
    await page.getByLabel('Metric', { exact: true }).selectOption('net_margin_pct');
    await expect(page.getByTestId('financial-chart').locator('svg').first()).toBeVisible();
    await page.getByRole('button', { name: 'Statements', exact: true }).click();
    await page.getByRole('button', { name: 'Cash flow', exact: true }).click();
    await expect(page.getByRole('table')).toBeVisible();
    await expect(page.getByRole('table').getByText('$0.00').first()).toBeVisible();
    await page.getByRole('button', { name: 'Compare', exact: true }).click();
    await expect(page.getByText('Fiscal intervals differ.')).toBeVisible();
    await expect(page.getByTestId('comparison-performance-chart').locator('svg').first()).toBeVisible();
    await expect(page.getByTestId('comparison-financial-chart').locator('svg').first()).toBeVisible();
    expect(errors).toEqual([]);
  });

  test('keeps financial evidence visible after a market error and exposes cutoff failures', async ({ page }) => {
    await page.route('**/api/companies/*/market?**', route => route.fulfill({ status: 404, json: { detail: { code: 'market_not_found', message: 'No saved market series matches these filters.' } } }));
    await page.goto('/');
    await expect(page.getByText('Market evidence unavailable')).toBeVisible();
    await expect(page.getByRole('button', { name: /Revenue: \$120B/ })).toBeVisible();
    await page.route('**/api/companies/*/financials?**', route => route.fulfill({ status: 409, json: { detail: { code: 'historical_evidence_unavailable', message: 'No local evidence was observed by this cutoff.' } } }));
    await page.getByLabel('Observed by', { exact: true }).fill('2020-01-01');
    await expect(page.getByText('No local evidence was observed by this cutoff.')).toBeVisible();
    await expect(page.getByText(/historical_evidence_unavailable/)).toBeVisible();
  });

  test('fits a narrow screen and contains statement overflow within its table', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/');
    await expect(page.getByTestId('price-chart').locator('canvas').first()).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.getByRole('button', { name: 'Statements', exact: true }).click();
    await expect(page.getByRole('table')).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.getByRole('button', { name: 'Compare', exact: true }).click();
    await expect(page.getByTestId('comparison-performance-chart')).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
});
