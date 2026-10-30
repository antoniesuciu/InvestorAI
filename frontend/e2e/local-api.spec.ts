import { expect, test } from '@playwright/test';

test('uses existing local reported evidence end to end without external browser requests', async ({ page }) => {
  test.skip(process.env.DASHBOARD_TEST_LOCAL_API !== '1', 'Opt in with DASHBOARD_TEST_LOCAL_API=1 and the local API running on 127.0.0.1:8000.');
  test.setTimeout(60_000);
  const externalRequests: string[] = [];
  const errors: string[] = [];
  page.on('request', request => { if (!new URL(request.url()).hostname.match(/^(127\.0\.0\.1|localhost)$/)) externalRequests.push(request.url()); });
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await expect(page.getByLabel('Company', { exact: true })).toContainText('Apple');
  await expect(page.getByTestId('price-chart').locator('canvas').first()).toBeVisible();
  await expect(page.getByText('Synthetic test data')).toHaveCount(0);
  await expect(page.getByText('Historical coverage').last()).toBeVisible();
  await page.getByRole('button', { name: /^Revenue: / }).click();
  const dialog = page.getByRole('dialog', { name: 'Evidence & provenance' });
  await expect(dialog.getByRole('link', { name: 'Original SEC filing' }).first()).toHaveAttribute('href', /^https:\/\/www.sec.gov\/Archives\//);
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: 'Financials', exact: true }).click();
  await page.getByLabel('Frequency').selectOption('quarterly');
  await expect(page.getByText('Recent observations')).toBeVisible();
  await page.getByRole('button', { name: 'Statements', exact: true }).click();
  await expect(page.getByRole('table')).toBeVisible();
  await page.getByRole('button', { name: 'Compare', exact: true }).click();
  await expect(page.getByTestId('comparison-performance-chart').locator('svg').first()).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId('comparison-financial-chart').locator('svg').first()).toBeVisible();
  expect(externalRequests).toEqual([]);
  expect(errors).toEqual([]);
});
