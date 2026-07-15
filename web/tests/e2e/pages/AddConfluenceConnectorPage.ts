import { expect, type Locator, type Page } from "@playwright/test";

/**
 * Page object for `/admin/connectors/confluence` create flow.
 * Locators stay here; specs only drive user actions + assertions.
 */
export class AddConfluenceConnectorPage {
  readonly page: Page;

  constructor(page: Page) {
    this.page = page;
  }

  async goto(): Promise<void> {
    await this.page.goto("/admin/connectors/confluence");
    await expect(this.page.getByText("Select a credential")).toBeVisible({
      timeout: 15000,
    });
  }

  credentialRadio(credentialName: string): Locator {
    return this.page
      .locator("tr")
      .filter({ hasText: credentialName })
      .getByRole("radio");
  }

  async selectCredential(credentialName: string): Promise<void> {
    await this.credentialRadio(credentialName).click();
    await expect(
      this.page
        .locator("tr")
        .filter({ hasText: credentialName })
        .getByText("selected")
    ).toBeVisible();
    await expect(
      this.page.getByRole("button", { name: "Continue" })
    ).toBeEnabled();
  }

  async continueFromCredentials(): Promise<void> {
    await this.page.getByRole("button", { name: "Continue" }).click();
    await expect(this.page.getByLabel("Connector Name")).toBeVisible({
      timeout: 10000,
    });
  }

  async fillConnectorConfig(opts: {
    name: string;
    wikiBaseUrl: string;
  }): Promise<void> {
    await this.page.getByLabel("Connector Name").fill(opts.name);
    await this.page.getByLabel("Wiki Base URL").fill(opts.wikiBaseUrl);
  }

  async createConnector(): Promise<void> {
    const createButton = this.page.getByRole("button", {
      name: "Create Connector",
    });
    await expect(createButton).toBeEnabled({ timeout: 10000 });
    await createButton.click();
  }

  toastContainer(): Locator {
    return this.page.getByTestId("toast-container");
  }

  async expectNoTimeoutToast(): Promise<void> {
    await expect(
      this.toastContainer().getByText(/Operation timed out/i)
    ).toHaveCount(0);
  }

  async expectRedirectedToIndexingStatus(): Promise<void> {
    await expect(this.page).toHaveURL(
      /\/admin\/indexing\/status\?message=connector-created/,
      { timeout: 60000 }
    );
  }
}
