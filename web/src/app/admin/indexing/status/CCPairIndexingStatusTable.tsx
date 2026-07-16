import React, { useState } from "react";
import {
  Table,
  TableRow,
  TableHead,
  TableBody,
  TableCell,
  TableHeader,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { CCPairStatus } from "@/components/Status";
import { timeAgo } from "@opal/time";
import {
  ValidSources,
  ConnectorIndexingStatusLiteResponse,
  SourceSummary,
  ConnectorIndexingStatusLite,
  FederatedConnectorStatus,
  BulkActionOutcome,
  BulkActionResponse,
} from "@/lib/types";
import type { Route } from "next";
import { useRouter } from "next/navigation";
import Truncated from "@/refresh-components/texts/Truncated";
import {
  FiChevronDown,
  FiChevronRight,
  FiLock,
  FiUnlock,
  FiRefreshCw,
} from "react-icons/fi";
import { Tooltip, Text } from "@opal/components";
import { SourceIcon } from "@/components/SourceIcon";
import { getSourceDisplayName } from "@/lib/sources";
import { useTierAtLeast } from "@/hooks/useTierAtLeast";
import { Tier } from "@/lib/settings/types";
import { ConnectorCredentialPairStatus } from "../../connector/[ccPairId]/types";
import { PageSelector } from "@/components/PageSelector";
import { ConnectorStaggeredSkeleton } from "./ConnectorRowSkeleton";
import { Button } from "@opal/components";
import {
  SvgSettings,
  SvgPauseCircle,
  SvgPlayCircle,
  SvgTrash,
  SvgAlertTriangle,
} from "@opal/icons";
import { toast } from "@/hooks/useToast";
import { bulkSetCCPairStatusForSource, setCCPairStatus } from "@/lib/ccPair";
import {
  bulkDeleteConnectorsForSource,
  deleteCCPair,
} from "@/lib/documentDeletion";
import { ConfirmEntityModal } from "@/sections/modals/ConfirmEntityModal";
import ConfirmationModalLayout from "@/refresh-components/layouts/ConfirmationModalLayout";

// Helper to handle navigation with cmd/ctrl+click support
// NOTE: using this rather than Next/Link (or similar) since shadcn
// table row components must be direct descendants of the table component
// and putting the <Link> inside the <TableRow> would causes some parts of the
// row to not navigate as expected.
function navigateWithModifier(
  e: React.MouseEvent,
  url: string,
  router: ReturnType<typeof useRouter>
) {
  if (e.metaKey || e.ctrlKey) {
    window.open(url, "_blank");
  } else {
    router.push(url as Route);
  }
}

function isFederatedConnectorStatus(
  status: ConnectorIndexingStatusLite | FederatedConnectorStatus
) {
  return status.name?.toLowerCase().includes("federated");
}

const NUMBER_OF_ROWS_PER_PAGE = 10;
const NUMBER_OF_COLUMNS = 6;

const OUTCOME_LABELS: Record<BulkActionOutcome, string> = {
  [BulkActionOutcome.SUCCEEDED]: "Succeeded",
  [BulkActionOutcome.SKIPPED]: "Skipped (no change needed)",
  [BulkActionOutcome.REJECTED]: "Not done — pause the connector first",
  [BulkActionOutcome.WARNING]: "Scheduled, but file cleanup had a problem",
  [BulkActionOutcome.FAILED]: "Failed",
};

function BulkResultModal({
  result,
  onClose,
}: {
  result: BulkActionResponse;
  onClose: () => void;
}) {
  // Only surface the outcomes the user needs to act on / be aware of.
  const problemOutcomes: BulkActionOutcome[] = [
    BulkActionOutcome.REJECTED,
    BulkActionOutcome.WARNING,
    BulkActionOutcome.FAILED,
  ];

  return (
    <ConfirmationModalLayout
      icon={SvgAlertTriangle}
      title="Bulk action results"
      onClose={onClose}
      hideCancel
      submit={<Button onClick={onClose}>Close</Button>}
    >
      <div className="flex flex-col gap-4">
        <Text font="main-ui-body" color="text-03">
          {`${result.succeeded} succeeded, ${result.skipped} skipped, ${result.rejected} need pausing, ${result.warning} with warnings, ${result.failed} failed.`}
        </Text>
        {problemOutcomes.map((outcome) => {
          const items = result.results.filter((r) => r.outcome === outcome);
          if (items.length === 0) return null;
          return (
            <div key={outcome} className="flex flex-col gap-1">
              <Text font="main-ui-action" color="text-04">
                {`${OUTCOME_LABELS[outcome]} (${items.length})`}
              </Text>
              <ul className="flex flex-col gap-1 pl-4 list-disc">
                {items.map((item) => (
                  <li key={item.cc_pair_id}>
                    <Text font="main-ui-body" color="text-03">
                      {item.message ? `${item.name} — ${item.message}` : item.name}
                    </Text>
                  </li>
                ))}
              </ul>
            </div>
          );
        })}
      </div>
    </ConfirmationModalLayout>
  );
}

function SummaryRow({
  source,
  summary,
  isOpen,
  onToggle,
  onActionComplete,
}: {
  source: ValidSources;
  summary: SourceSummary;
  isOpen: boolean;
  onToggle: () => void;
  onActionComplete?: () => void;
}) {
  const businessTier = useTierAtLeast(Tier.BUSINESS);

  const [actingType, setActingType] = useState<"status" | "delete" | null>(
    null
  );
  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [resultDetails, setResultDetails] = useState<BulkActionResponse | null>(
    null
  );

  const isActing = actingType !== null;
  const anyActive = summary.active_connectors > 0;
  const allPaused = summary.active_connectors === 0;
  const hasConnectors = summary.total_connectors > 0;
  const displayName = getSourceDisplayName(source);

  function reportResult(result: BulkActionResponse, verb: string) {
    const problems = result.rejected + result.warning + result.failed;
    if (problems === 0) {
      toast.success(
        `${verb} ${result.succeeded} connector${
          result.succeeded === 1 ? "" : "s"
        }${result.skipped ? ` (${result.skipped} unchanged)` : ""}`
      );
    } else {
      toast.error(
        `${verb}: ${result.succeeded} succeeded, ${result.rejected} need pausing, ${result.warning} with warnings, ${result.failed} failed`
      );
      setResultDetails(result);
    }
    onActionComplete?.();
  }

  async function handleBulkStatus() {
    const targetStatus = anyActive
      ? ConnectorCredentialPairStatus.PAUSED
      : ConnectorCredentialPairStatus.ACTIVE;
    setActingType("status");
    try {
      const result = await bulkSetCCPairStatusForSource(source, targetStatus);
      reportResult(result, anyActive ? "Paused" : "Resumed");
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "Failed to update connectors"
      );
    } finally {
      setActingType(null);
    }
  }

  async function handleBulkDelete() {
    setShowDeleteModal(false);
    setActingType("delete");
    try {
      const result = await bulkDeleteConnectorsForSource(source);
      reportResult(result, "Scheduled deletion for");
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : "Failed to schedule connector deletions"
      );
    } finally {
      setActingType(null);
    }
  }

  return (
    <TableRow
      onClick={onToggle}
      className="border-border dark:hover:bg-neutral-800 dark:border-neutral-700 group hover:bg-background-settings-hover/20 bg-background-sidebar py-4 rounded-xs border! cursor-pointer"
    >
      <TableCell>
        <div className="text-xl flex items-center truncate ellipsis gap-x-2 font-semibold">
          <div className="cursor-pointer">
            {isOpen ? (
              <FiChevronDown size={20} />
            ) : (
              <FiChevronRight size={20} />
            )}
          </div>
          <SourceIcon iconSize={20} sourceType={source} />
          {displayName}
        </div>
      </TableCell>

      <TableCell>
        <div className="text-sm text-neutral-500 dark:text-neutral-300">
          Total Connectors
        </div>
        <div className="text-xl font-semibold">{summary.total_connectors}</div>
      </TableCell>

      <TableCell>
        <div className="text-sm text-neutral-500 dark:text-neutral-300">
          Active Connectors
        </div>
        <p className="flex text-xl mx-auto font-semibold items-center text-lg mt-1">
          {summary.active_connectors}/{summary.total_connectors}
        </p>
      </TableCell>

      {businessTier && (
        <TableCell>
          <div className="text-sm text-neutral-500 dark:text-neutral-300">
            Public Connectors
          </div>
          <p className="flex text-xl mx-auto font-semibold items-center text-lg mt-1">
            {summary.public_connectors}/{summary.total_connectors}
          </p>
        </TableCell>
      )}

      <TableCell>
        <div className="text-sm text-neutral-500 dark:text-neutral-300">
          Total Docs Indexed
        </div>
        <div className="text-xl font-semibold">
          {summary.total_docs_indexed.toLocaleString()}
        </div>
      </TableCell>

      <TableCell>
        <div
          className="flex items-center justify-end gap-x-2"
          onClick={(e) => e.stopPropagation()}
        >
          <Button
            prominence="secondary"
            size="sm"
            icon={anyActive ? SvgPauseCircle : SvgPlayCircle}
            disabled={!hasConnectors || isActing}
            onClick={handleBulkStatus}
          >
            {actingType === "status"
              ? "Working\u2026"
              : anyActive
                ? "Pause All"
                : "Resume All"}
          </Button>
          <Button
            variant="danger"
            prominence="secondary"
            size="sm"
            icon={SvgTrash}
            disabled={!hasConnectors || !allPaused || isActing}
            tooltip={
              !allPaused ? "Pause all connectors before deleting" : undefined
            }
            onClick={() => setShowDeleteModal(true)}
          >
            {actingType === "delete" ? "Working\u2026" : "Delete All"}
          </Button>

          {showDeleteModal && (
            <ConfirmEntityModal
              danger
              entityType="Connectors"
              entityName={`${displayName} (${summary.total_connectors} connector${
                summary.total_connectors === 1 ? "" : "s"
              })`}
              additionalDetails="This schedules a deletion job for every connector in this vendor, removing their indexed documents. This cannot be undone."
              actionButtonText="Delete All"
              onClose={() => setShowDeleteModal(false)}
              onSubmit={handleBulkDelete}
            />
          )}

          {resultDetails && (
            <BulkResultModal
              result={resultDetails}
              onClose={() => setResultDetails(null)}
            />
          )}
        </div>
      </TableCell>
    </TableRow>
  );
}

function ConnectorRow({
  ccPairsIndexingStatus,
  invisible,
  isEditable,
  onActionComplete,
}: {
  ccPairsIndexingStatus: ConnectorIndexingStatusLite;
  invisible?: boolean;
  isEditable: boolean;
  onActionComplete?: () => void;
}) {
  const router = useRouter();
  const businessTier = useTierAtLeast(Tier.BUSINESS);

  const [actingType, setActingType] = useState<"status" | "delete" | null>(
    null
  );
  const [showDeleteModal, setShowDeleteModal] = useState(false);

  const isActing = actingType !== null;
  const isActive =
    ccPairsIndexingStatus.cc_pair_status === ConnectorCredentialPairStatus.ACTIVE;
  const isDeleting =
    ccPairsIndexingStatus.cc_pair_status === ConnectorCredentialPairStatus.DELETING;

  const connectorUrl = `/admin/connector/${ccPairsIndexingStatus.cc_pair_id}`;

  const handleRowClick = (e: React.MouseEvent) => {
    navigateWithModifier(e, connectorUrl, router);
  };

  async function handleToggleStatus() {
    setActingType("status");
    try {
      await setCCPairStatus(
        ccPairsIndexingStatus.cc_pair_id,
        isActive
          ? ConnectorCredentialPairStatus.PAUSED
          : ConnectorCredentialPairStatus.ACTIVE,
        onActionComplete
      );
    } finally {
      setActingType(null);
    }
  }

  async function handleDelete() {
    setShowDeleteModal(false);
    setActingType("delete");
    try {
      await deleteCCPair(
        ccPairsIndexingStatus.connector_id,
        ccPairsIndexingStatus.credential_id,
        onActionComplete
      );
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : "Failed to schedule connector deletion"
      );
    } finally {
      setActingType(null);
    }
  }

  return (
    <TableRow
      className={`
  border border-border dark:border-neutral-700
          hover:bg-accent-background ${
            invisible
              ? "invisible h-0! -mb-10! border-none!"
              : "border! border-border dark:border-neutral-700"
          }  w-full cursor-pointer relative `}
      onClick={handleRowClick}
    >
      <TableCell className="">
        <Truncated>{ccPairsIndexingStatus.name}</Truncated>
      </TableCell>
      <TableCell>
        {timeAgo(ccPairsIndexingStatus?.last_success) || "-"}
      </TableCell>
      <TableCell>
        <CCPairStatus
          ccPairStatus={
            ccPairsIndexingStatus.last_finished_status !== null
              ? ccPairsIndexingStatus.cc_pair_status
              : ccPairsIndexingStatus.last_status == "not_started"
                ? ConnectorCredentialPairStatus.SCHEDULED
                : ConnectorCredentialPairStatus.INITIAL_INDEXING
          }
          inRepeatedErrorState={ccPairsIndexingStatus.in_repeated_error_state}
          lastIndexAttemptStatus={ccPairsIndexingStatus.last_status}
        />
      </TableCell>
      {businessTier && (
        <TableCell>
          {ccPairsIndexingStatus.access_type === "public" ? (
            <Badge variant={isEditable ? "success" : "default"} icon={FiUnlock}>
              Organization Public
            </Badge>
          ) : ccPairsIndexingStatus.access_type === "sync" ? (
            <Badge
              variant={isEditable ? "auto-sync" : "default"}
              icon={FiRefreshCw}
            >
              Inherited from{" "}
              {getSourceDisplayName(ccPairsIndexingStatus.source)}
            </Badge>
          ) : (
            <Badge variant={isEditable ? "private" : "default"} icon={FiLock}>
              Private
            </Badge>
          )}
        </TableCell>
      )}
      <TableCell>{ccPairsIndexingStatus.docs_indexed}</TableCell>
      <TableCell>
        {isEditable && (
          <div
            className="flex items-center justify-end gap-x-1"
            onClick={(e) => e.stopPropagation()}
          >
            <Tooltip tooltip={isActive ? "Pause Connector" : "Resume Connector"}>
              <Button
                icon={isActive ? SvgPauseCircle : SvgPlayCircle}
                prominence="tertiary"
                disabled={isActing || isDeleting}
                onClick={handleToggleStatus}
              />
            </Tooltip>
            <Tooltip
              tooltip={
                isActive
                  ? "Pause the connector before deleting"
                  : "Delete Connector"
              }
            >
              <Button
                icon={SvgTrash}
                prominence="tertiary"
                disabled={isActing || isDeleting || isActive}
                onClick={() => setShowDeleteModal(true)}
              />
            </Tooltip>
            <Tooltip tooltip="Manage Connector">
              <Button
                icon={SvgSettings}
                prominence="tertiary"
                onClick={(e: React.MouseEvent) => {
                  e.stopPropagation();
                  navigateWithModifier(e, connectorUrl, router);
                }}
              />
            </Tooltip>

            {showDeleteModal && (
              <ConfirmEntityModal
                danger
                entityType="Connector"
                entityName={ccPairsIndexingStatus.name}
                additionalDetails="This schedules a deletion job for this connector, removing its indexed documents. This cannot be undone."
                actionButtonText="Delete"
                onClose={() => setShowDeleteModal(false)}
                onSubmit={handleDelete}
              />
            )}
          </div>
        )}
      </TableCell>
    </TableRow>
  );
}

function FederatedConnectorRow({
  federatedConnector,
  invisible,
}: {
  federatedConnector: FederatedConnectorStatus;
  invisible?: boolean;
}) {
  const router = useRouter();
  const businessTier = useTierAtLeast(Tier.BUSINESS);

  const federatedUrl = `/admin/federated/${federatedConnector.id}`;

  const handleRowClick = (e: React.MouseEvent) => {
    navigateWithModifier(e, federatedUrl, router);
  };

  return (
    <TableRow
      className={`
  border border-border dark:border-neutral-700
          hover:bg-accent-background ${
            invisible
              ? "invisible h-0! -mb-10! border-none!"
              : "border! border-border dark:border-neutral-700"
          }  w-full cursor-pointer relative `}
      onClick={handleRowClick}
    >
      <TableCell className="">
        <Truncated>{federatedConnector.name}</Truncated>
      </TableCell>
      <TableCell>N/A</TableCell>
      <TableCell>
        <Badge variant="success">Indexed</Badge>
      </TableCell>
      {businessTier && (
        <TableCell>
          <Badge variant="secondary" icon={FiRefreshCw}>
            Federated Access
          </Badge>
        </TableCell>
      )}
      <TableCell>N/A</TableCell>
      <TableCell>
        <Button
          icon={SvgSettings}
          prominence="tertiary"
          onClick={(e: React.MouseEvent) => {
            e.stopPropagation();
            navigateWithModifier(e, federatedUrl, router);
          }}
          tooltip="Manage Federated Connector"
        />
      </TableCell>
    </TableRow>
  );
}

export function CCPairIndexingStatusTable({
  ccPairsIndexingStatuses,
  connectorsToggled,
  toggleSource,
  onPageChange,
  sourceLoadingStates = {} as Record<ValidSources, boolean>,
  onActionComplete,
}: {
  ccPairsIndexingStatuses: ConnectorIndexingStatusLiteResponse[];
  connectorsToggled: Record<ValidSources, boolean>;
  toggleSource: (source: ValidSources, toggled?: boolean | null) => void;
  onPageChange: (source: ValidSources, newPage: number) => void;
  sourceLoadingStates?: Record<ValidSources, boolean>;
  onActionComplete?: () => void;
}) {
  const businessTier = useTierAtLeast(Tier.BUSINESS);

  return (
    <Table className="-mt-8 table-fixed">
      <TableHeader>
        <ConnectorRow
          invisible
          ccPairsIndexingStatus={{
            cc_pair_id: 1,
            connector_id: 1,
            credential_id: 1,
            name: "Sample File Connector",
            cc_pair_status: ConnectorCredentialPairStatus.ACTIVE,
            last_status: "success",
            source: ValidSources.File,
            access_type: "public",
            docs_indexed: 1000,
            last_success: "2023-07-01T12:00:00Z",
            last_finished_status: "success",
            is_editable: false,
            in_repeated_error_state: false,
            in_progress: false,
            latest_index_attempt_docs_indexed: 0,
          }}
          isEditable={false}
        />
      </TableHeader>
      <TableBody>
        {ccPairsIndexingStatuses.map((ccPairStatus) => (
          <React.Fragment key={ccPairStatus.source}>
            <TableRow className="border-none">
              <TableCell
                colSpan={
                  businessTier ? NUMBER_OF_COLUMNS : NUMBER_OF_COLUMNS - 1
                }
                className="h-4 p-0"
              />
            </TableRow>
            <SummaryRow
              source={ccPairStatus.source}
              summary={ccPairStatus.summary}
              isOpen={connectorsToggled[ccPairStatus.source] || false}
              onToggle={() => toggleSource(ccPairStatus.source)}
              onActionComplete={onActionComplete}
            />
            {connectorsToggled[ccPairStatus.source] && (
              <>
                {sourceLoadingStates[ccPairStatus.source] && (
                  <ConnectorStaggeredSkeleton rowCount={8} height="h-[79px]" />
                )}
                {!sourceLoadingStates[ccPairStatus.source] && (
                  <>
                    <TableRow className="border border-border dark:border-neutral-700">
                      <TableHead>Name</TableHead>
                      <TableHead>Last Indexed</TableHead>
                      <TableHead>Status</TableHead>
                      {businessTier && (
                        <TableHead>Permissions / Access</TableHead>
                      )}
                      <TableHead>Total Docs</TableHead>
                      <TableHead></TableHead>
                    </TableRow>
                    {ccPairStatus.indexing_statuses.map((indexingStatus) => {
                      if (isFederatedConnectorStatus(indexingStatus)) {
                        const status =
                          indexingStatus as FederatedConnectorStatus;
                        return (
                          <FederatedConnectorRow
                            key={status.id}
                            federatedConnector={status}
                          />
                        );
                      } else {
                        const status =
                          indexingStatus as ConnectorIndexingStatusLite;
                        return (
                          <ConnectorRow
                            key={status.cc_pair_id}
                            ccPairsIndexingStatus={status}
                            isEditable={status.is_editable}
                            onActionComplete={onActionComplete}
                          />
                        );
                      }
                    })}
                    {/* Add dummy rows to reach 10 total rows for cleaner UI */}
                    {ccPairStatus.indexing_statuses.length <
                      NUMBER_OF_ROWS_PER_PAGE &&
                      ccPairStatus.total_pages > 1 &&
                      Array.from({
                        length:
                          NUMBER_OF_ROWS_PER_PAGE -
                          ccPairStatus.indexing_statuses.length,
                      }).map((_, index) => {
                        const isLastDummyRow =
                          index ===
                          NUMBER_OF_ROWS_PER_PAGE -
                            ccPairStatus.indexing_statuses.length -
                            1;
                        return (
                          <TableRow
                            key={`dummy-${ccPairStatus.source}-${index}`}
                            className={
                              isLastDummyRow
                                ? "border-l border-r border-b border-border dark:border-neutral-700"
                                : "border-l border-r border-t-0 border-b-0 border-border dark:border-neutral-700"
                            }
                            style={
                              isLastDummyRow
                                ? {
                                    borderBottom: "1px solid var(--border)",
                                    borderRight: "1px solid var(--border)",
                                    borderLeft: "1px solid var(--border)",
                                  }
                                : {}
                            }
                          >
                            {isLastDummyRow ? (
                              <TableCell
                                colSpan={
                                  businessTier
                                    ? NUMBER_OF_COLUMNS
                                    : NUMBER_OF_COLUMNS - 1
                                }
                                className="h-[56px] text-center text-sm text-gray-400 dark:text-gray-500 border-b border-r border-l border-border dark:border-neutral-700"
                              >
                                <span className="italic">
                                  All caught up! No more connectors to show
                                </span>
                              </TableCell>
                            ) : (
                              <>
                                <TableCell className="h-[56px]"></TableCell>
                                <TableCell></TableCell>
                                <TableCell></TableCell>
                                {businessTier && <TableCell></TableCell>}
                                <TableCell></TableCell>
                                <TableCell></TableCell>
                              </>
                            )}
                          </TableRow>
                        );
                      })}
                  </>
                )}
                {ccPairStatus.total_pages > 1 && (
                  <TableRow className="border-l border-r border-b border-border dark:border-neutral-700">
                    <TableCell
                      colSpan={
                        businessTier ? NUMBER_OF_COLUMNS : NUMBER_OF_COLUMNS - 1
                      }
                    >
                      <div className="flex justify-center">
                        <PageSelector
                          currentPage={ccPairStatus.current_page}
                          totalPages={ccPairStatus.total_pages}
                          onPageChange={(newPage) =>
                            onPageChange(ccPairStatus.source, newPage)
                          }
                        />
                      </div>
                    </TableCell>
                  </TableRow>
                )}
              </>
            )}
          </React.Fragment>
        ))}
      </TableBody>
    </Table>
  );
}
