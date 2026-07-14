import { ConnectorCredentialPairStatus } from "@/app/admin/connector/[ccPairId]/types";
import { toast } from "@/hooks/useToast";
import { BulkActionResponse, ValidSources } from "@/lib/types";

export async function setCCPairStatus(
  ccPairId: number,
  ccPairStatus: ConnectorCredentialPairStatus,
  onUpdate?: () => void
) {
  try {
    const response = await fetch(
      `/api/manage/admin/cc-pair/${ccPairId}/status`,
      {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ status: ccPairStatus }),
      }
    );

    if (!response.ok) {
      const { detail } = await response.json();
      toast.error(`Failed to update connector status - ${detail}`);
      return;
    }

    toast.success(
      ccPairStatus === ConnectorCredentialPairStatus.ACTIVE
        ? "Enabled connector!"
        : "Paused connector!"
    );

    onUpdate?.();
  } catch (error) {
    console.error("Error updating CC pair status:", error);
    toast.error("Failed to update connector status");
  }
}

// Pause/resume every editable connector for a vendor (source) in one call.
// Returns per-connector outcomes; throws only on a total request failure.
export async function bulkSetCCPairStatusForSource(
  source: ValidSources,
  status: ConnectorCredentialPairStatus
): Promise<BulkActionResponse> {
  const response = await fetch(`/api/manage/admin/cc-pair/bulk-status`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ source, status }),
  });

  if (!response.ok) {
    const detail = await response
      .json()
      .then((body) => body?.detail)
      .catch(() => undefined);
    throw new Error(detail || "Failed to update connectors");
  }

  return response.json();
}

export const getCCPairStatusMessage = (
  isDisabled: boolean,
  isIndexing: boolean,
  ccPairStatus: ConnectorCredentialPairStatus
) => {
  if (ccPairStatus === ConnectorCredentialPairStatus.INVALID) {
    return "Connector is in an invalid state. Please update the credentials or configuration before re-indexing.";
  }
  if (ccPairStatus === ConnectorCredentialPairStatus.DELETING) {
    return "Cannot index while connector is deleting";
  }
  if (isIndexing) {
    return "Indexing is already in progress";
  }
  if (isDisabled) {
    return "Connector must be re-enabled before indexing";
  }
  return undefined;
};
