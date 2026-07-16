import { test, expect } from "@playwright/test";
import { loginAs } from "@tests/e2e/utils/auth";
import { OnyxApiClient } from "@tests/e2e/utils/onyxApiClient";

/**
 * End-to-end coverage for the Search mode multi-select source filter.
 *
 * Seeds one document per source (Jira, Github, Confluence) sharing a unique
 * query marker, then drives the UI: default (all sources) -> single-select
 * scoping -> multi-select scoping -> clearing back to all sources. Each
 * source needs its own real CC pair (not just a document-level `source`
 * override) since the Sources popover list is populated from configured
 * connectors, not from ingested documents.
 */
test.describe("Search mode source filter", () => {
  test("scopes results to selected sources and clears back to all sources", async ({
    page,
  }) => {
    await page.context().clearCookies();
    await loginAs(page, "admin2");

    const apiClient = new OnyxApiClient(page.request);
    const marker = `e2e-source-filter-${Date.now()}`;

    const jiraTitle = "E2E Source Filter Doc Alpha";
    const githubTitle = "E2E Source Filter Doc Beta";
    const confluenceTitle = "E2E Source Filter Doc Gamma";

    const jiraCcPairId = await apiClient.createMockConnector(
      "jira",
      `E2E Source Filter Jira Connector ${marker}`
    );
    const githubCcPairId = await apiClient.createMockConnector(
      "github",
      `E2E Source Filter Github Connector ${marker}`
    );
    const confluenceCcPairId = await apiClient.createMockConnector(
      "confluence",
      `E2E Source Filter Confluence Connector ${marker}`
    );

    await apiClient.seedIngestionDocument({
      ccPairId: jiraCcPairId,
      documentId: `${marker}-jira`,
      source: "jira",
      semanticIdentifier: jiraTitle,
      content: `${marker} content from jira`,
    });
    await apiClient.seedIngestionDocument({
      ccPairId: githubCcPairId,
      documentId: `${marker}-github`,
      source: "github",
      semanticIdentifier: githubTitle,
      content: `${marker} content from github`,
    });
    await apiClient.seedIngestionDocument({
      ccPairId: confluenceCcPairId,
      documentId: `${marker}-confluence`,
      source: "confluence",
      semanticIdentifier: confluenceTitle,
      content: `${marker} content from confluence`,
    });

    await apiClient.setDefaultAppMode("SEARCH");

    try {
      await page.goto("/app");
      await page.waitForLoadState("networkidle");

      const textbox = page.locator("#onyx-chat-input-textbox");
      await textbox.click();
      await textbox.fill(marker);
      await page.keyboard.press("Enter");

      // Default (no filter): all three sources are represented.
      await expect(page.getByText(jiraTitle)).toBeVisible({ timeout: 15000 });
      await expect(page.getByText(githubTitle)).toBeVisible();
      await expect(page.getByText(confluenceTitle)).toBeVisible();

      const sourceFilterTrigger = page.getByTestId("source-filter-trigger");
      await expect(sourceFilterTrigger).toBeVisible();

      // Single-select: Jira only.
      await sourceFilterTrigger.click();
      await page.getByText("Jira", { exact: true }).click();
      await page.keyboard.press("Escape");

      await expect(page.getByText(jiraTitle)).toBeVisible();
      await expect(page.getByText(githubTitle)).not.toBeVisible();
      await expect(page.getByText(confluenceTitle)).not.toBeVisible();

      // Multi-select: add Github alongside the existing Jira selection.
      await sourceFilterTrigger.click();
      await page.getByText("Github", { exact: true }).click();
      await page.keyboard.press("Escape");

      await expect(page.getByText(jiraTitle)).toBeVisible();
      await expect(page.getByText(githubTitle)).toBeVisible();
      await expect(page.getByText(confluenceTitle)).not.toBeVisible();

      // Clear: back to all-sources behavior.
      await sourceFilterTrigger.click();
      await page.getByText("All Sources", { exact: true }).click();

      await expect(page.getByText(jiraTitle)).toBeVisible();
      await expect(page.getByText(githubTitle)).toBeVisible();
      await expect(page.getByText(confluenceTitle)).toBeVisible();
    } finally {
      await apiClient.setDefaultAppMode("CHAT");
      await apiClient.deleteCCPair(jiraCcPairId);
      await apiClient.deleteCCPair(githubCcPairId);
      await apiClient.deleteCCPair(confluenceCcPairId);
    }
  });
});
