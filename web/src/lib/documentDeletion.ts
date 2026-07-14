import { toast } from "@/hooks/useToast";
import {
  BulkActionResponse,
  DeletionAttemptSnapshot,
  ValidSources,
} from "./types";

export async function scheduleDeletionJobForConnector(
  connectorId: number,
  credentialId: number
) {
  // Will schedule a background job which will:
  // 1. Remove all documents indexed by the connector / credential pair
  // 2. Remove the connector (if this is the only pair using the connector)
  const response = await fetch(`/api/manage/admin/deletion-attempt`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      connector_id: connectorId,
      credential_id: credentialId,
    }),
  });
  if (response.ok) {
    return null;
  }
  return (await response.json()).detail;
}

export async function deleteCCPair(
  connectorId: number,
  credentialId: number,
  onCompletion?: () => void
) {
  const deletionScheduleError = await scheduleDeletionJobForConnector(
    connectorId,
    credentialId
  );
  if (deletionScheduleError) {
    throw new Error(deletionScheduleError);
  }
  toast.success("Scheduled deletion of connector!");
  onCompletion?.();
}

// Schedule deletion for every eligible connector of a vendor (source) in one
// call. Returns per-connector outcomes; throws only on a total request failure.
export async function bulkDeleteConnectorsForSource(
  source: ValidSources
): Promise<BulkActionResponse> {
  const response = await fetch(`/api/manage/admin/bulk-deletion-attempt`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ source }),
  });

  if (!response.ok) {
    const detail = await response
      .json()
      .then((body) => body?.detail)
      .catch(() => undefined);
    throw new Error(detail || "Failed to schedule connector deletions");
  }

  return response.json();
}

export function isCurrentlyDeleting(
  deletionAttempt: DeletionAttemptSnapshot | null
) {
  if (!deletionAttempt) {
    return false;
  }

  return (
    deletionAttempt.status === "PENDING" || deletionAttempt.status === "STARTED"
  );
}
