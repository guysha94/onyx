/**
 * Covers the Add Connector create+link race against CONNECTOR_CREATION_TIMEOUT_MS.
 *
 * Real Confluence Cloud validation routinely exceeds the old 10s UI budget
 * (especially with Auto Sync Permissions). CI has no Cloud credentials, so
 * this spec route-mocks create (fast) + credential link (intentionally slow
 * past the old 10s budget, under the new 45s budget) and asserts:
 * - no timeout toast
 * - no rollback DELETE of the just-created connector
 * - success redirect to indexing status
 *
 * Backend live-validation behavior is out of scope here; this guards the
 * frontend timeout/abort/rollback regression.
 */

import { test, expect, type Page, type Route } from "@playwright/test";

import { AddConfluenceConnectorPage } from "@tests/e2e/pages/AddConfluenceConnectorPage";

test.use({ storageState: "admin_auth.json" });

const MOCK_CREDENTIAL_ID = 4242;
const MOCK_CONNECTOR_ID = 7777;
const MOCK_CREDENTIAL_NAME = "E2E Mock Confluence Cred";
/** Longer than the historical 10s UI timeout; shorter than the new 45s budget. */
const SLOW_LINK_DELAY_MS = 12_000;

function jsonResponse(data: unknown, status = 200) {
  return {
    status,
    contentType: "application/json",
    body: JSON.stringify(data),
  };
}

function mockCredential() {
  const now = new Date().toISOString();
  return {
    id: MOCK_CREDENTIAL_ID,
    name: MOCK_CREDENTIAL_NAME,
    source: "confluence",
    user_id: null,
    user_email: null,
    time_created: now,
    time_updated: now,
    credential_json: {
      confluence_username: "e2e@example.com",
      confluence_access_token: "mock-token",
    },
    admin_public: true,
    curator_public: true,
  };
}

async function fulfillJson(route: Route, data: unknown, status = 200) {
  await route.fulfill(jsonResponse(data, status));
}

/**
 * Mocks the credential list + create/link endpoints used by the Confluence
 * create flow. Tracks DELETE so we can assert no timeout rollback.
 */
async function mockSlowButSuccessfulCreate(
  page: Page
): Promise<{ deleteCount: () => number }> {
  let deletes = 0;
  const credential = mockCredential();

  await page.route(
    "**/api/manage/admin/similar-credentials/confluence**",
    async (route) => {
      if (route.request().method() === "GET") {
        await fulfillJson(route, [credential]);
        return;
      }
      await route.continue();
    }
  );

  await page.route("**/api/manage/admin/connector", async (route) => {
    if (route.request().method() === "POST") {
      await fulfillJson(route, { id: MOCK_CONNECTOR_ID });
      return;
    }
    await route.continue();
  });

  await page.route(
    `**/api/manage/connector/${MOCK_CONNECTOR_ID}/credential/${MOCK_CREDENTIAL_ID}`,
    async (route) => {
      if (route.request().method() === "PUT") {
        await new Promise((resolve) =>
          setTimeout(resolve, SLOW_LINK_DELAY_MS)
        );
        await fulfillJson(route, {
          success: true,
          message: "ok",
          data: 1,
        });
        return;
      }
      await route.continue();
    }
  );

  await page.route(
    `**/api/manage/admin/connector/${MOCK_CONNECTOR_ID}`,
    async (route) => {
      if (route.request().method() === "DELETE") {
        deletes += 1;
        await fulfillJson(route, { success: true });
        return;
      }
      await route.continue();
    }
  );

  return { deleteCount: () => deletes };
}

test.describe("Connector creation timeout budget @exclusive", () => {
  test("create+link succeeds when link takes longer than the old 10s budget", async ({
    page,
  }) => {
    test.setTimeout(90_000);

    const mocks = await mockSlowButSuccessfulCreate(page);
    const addPage = new AddConfluenceConnectorPage(page);

    await addPage.goto();
    await addPage.selectCredential(MOCK_CREDENTIAL_NAME);
    await addPage.continueFromCredentials();
    await addPage.fillConnectorConfig({
      name: "e2e-confluence-timeout-budget",
      wikiBaseUrl: "https://example.atlassian.net/wiki",
    });

    const createStartedAt = Date.now();
    await addPage.createConnector();
    await addPage.expectRedirectedToIndexingStatus();
    const elapsedMs = Date.now() - createStartedAt;

    // Prove we waited through the old 10s budget without rolling back.
    expect(elapsedMs).toBeGreaterThan(SLOW_LINK_DELAY_MS - 500);
    expect(mocks.deleteCount()).toBe(0);
    await addPage.expectNoTimeoutToast();
  });
});
