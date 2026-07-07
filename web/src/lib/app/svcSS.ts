import type { Metadata } from "next";
import { SERVER_SIDE_ONLY__PAID_ENTERPRISE_FEATURES_ENABLED } from "@/lib/constants";
import { fetchEnterpriseSettingsSS } from "@/lib/settings/svcSS";

async function fetchAppName(): Promise<string> {
  if (SERVER_SIDE_ONLY__PAID_ENTERPRISE_FEATURES_ENABLED) {
    const enterprise = await fetchEnterpriseSettingsSS();
    if (enterprise?.application_name?.trim()) {
      return enterprise.application_name.trim();
    }
  }
  return "SuperPlay";
}

export async function generateFaviconMetadata(): Promise<Metadata["icons"]> {
  // "/favicon.ico" (web/public/favicon.ico) holds the whitelabeled default icon.
  // NOTE: "/onyx.ico" also exists in web/public but is the original, un-rebranded
  // asset — it must not be used as the default here.
  let iconSrc = "/favicon.ico";

  if (SERVER_SIDE_ONLY__PAID_ENTERPRISE_FEATURES_ENABLED) {
    const enterprise = await fetchEnterpriseSettingsSS();
    if (enterprise?.use_custom_logo) {
      iconSrc = "/api/enterprise-settings/logo";
    }
  }

  return { icon: iconSrc };
}

export async function generateAdminTitleMetadata(): Promise<Metadata["title"]> {
  return `Admin — ${await fetchAppName()}`;
}
