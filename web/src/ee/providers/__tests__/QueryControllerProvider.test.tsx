import React, { PropsWithChildren } from "react";
import { act, renderHook } from "@testing-library/react";
import { ValidSources } from "@/lib/types";
import { SearchDocWithContent } from "@/lib/search/interfaces";
import {
  QueryControllerProvider,
  useQueryController,
} from "@/providers/QueryControllerProvider";

const mockSearchDocuments = jest.fn();
const mockUseCCPairs = jest.fn();
const mockUseFederatedConnectors = jest.fn();

// Stable object so the `useEffect(reset, [appFocus, reset])` sync effect in
// the provider doesn't see a new `appFocus` identity on every render.
const stableNewSessionFocus = { isNewSession: () => true };

jest.mock("@/hooks/useAppFocus", () => ({
  __esModule: true,
  default: () => stableNewSessionFocus,
}));

jest.mock("@/hooks/useTierAtLeast", () => ({
  useTierAtLeast: () => true,
}));

jest.mock("@/lib/settings/hooks", () => ({
  useIsSearchModeAvailable: () => true,
}));

jest.mock("@/providers/UserProvider", () => ({
  useUser: () => ({
    user: { preferences: { default_app_mode: "search" } },
  }),
}));

jest.mock("@/ee/lib/search/svc", () => ({
  searchDocuments: (...args: unknown[]) => mockSearchDocuments(...args),
  classifyQuery: jest.fn(),
}));

jest.mock("@/hooks/useCCPairs", () => ({
  __esModule: true,
  default: (...args: unknown[]) => mockUseCCPairs(...args),
}));

jest.mock("@/lib/hooks", () => ({
  useFederatedConnectors: (...args: unknown[]) =>
    mockUseFederatedConnectors(...args),
}));

const wrapper = ({ children }: PropsWithChildren) => (
  <QueryControllerProvider>{children}</QueryControllerProvider>
);

function emptySearchResponse() {
  return Promise.resolve({
    all_executed_queries: [],
    search_docs: [],
    llm_selected_doc_ids: null,
    error: null,
  });
}

function makeDoc(
  id: string,
  source: ValidSources,
  score: number
): SearchDocWithContent {
  return {
    document_id: id,
    chunk_ind: 0,
    semantic_identifier: id,
    link: null,
    blurb: "",
    source_type: source,
    boost: 0,
    hidden: false,
    metadata: {},
    score,
    match_highlights: [],
    updated_at: null,
    is_internet: false,
  };
}

/** Configures the two connected-source hooks the provider fans out over. */
function setConnectedSources(
  ccPairSources: ValidSources[],
  federatedSources: ValidSources[] = []
) {
  mockUseCCPairs.mockReturnValue({
    ccPairs: ccPairSources.map((source) => ({ source })),
    isLoading: false,
    error: null,
    refetch: jest.fn(),
  });
  mockUseFederatedConnectors.mockReturnValue({
    data: federatedSources.map((source) => ({ source })),
    refreshFederatedConnectors: jest.fn(),
  });
}

describe("EE QueryControllerProvider search fan-out", () => {
  beforeEach(() => {
    mockSearchDocuments.mockReset();
    mockSearchDocuments.mockImplementation(emptySearchResponse);
    setConnectedSources([]);
  });

  it("falls back to a single unscoped search when there are no connected sources", async () => {
    const { result } = renderHook(() => useQueryController(), { wrapper });

    await act(async () => {
      await result.current.submit("first query", jest.fn());
    });

    expect(mockSearchDocuments).toHaveBeenCalledTimes(1);
    const [, options] = mockSearchDocuments.mock.calls[0]!;
    expect(options.filters).toEqual({
      time_cutoff: null,
      tags: null,
      document_set: null,
      source_type: null,
    });
    expect(result.current.sourceCounts).toEqual({});
  });

  it("fans out one search per connected source, each scoped to that source", async () => {
    setConnectedSources([ValidSources.Jira, ValidSources.GoogleDrive]);
    const { result } = renderHook(() => useQueryController(), { wrapper });

    await act(async () => {
      await result.current.submit("scoped query", jest.fn());
    });

    expect(mockSearchDocuments).toHaveBeenCalledTimes(2);
    const filtersBySource = mockSearchDocuments.mock.calls.map(
      ([, options]) => options.filters
    );
    expect(filtersBySource).toEqual(
      expect.arrayContaining([
        {
          time_cutoff: null,
          tags: null,
          document_set: null,
          source_type: [ValidSources.Jira],
        },
        {
          time_cutoff: null,
          tags: null,
          document_set: null,
          source_type: [ValidSources.GoogleDrive],
        },
      ])
    );
  });

  it("includes federated connectors in the fan-out alongside regular CC pairs", async () => {
    setConnectedSources([ValidSources.Jira], [ValidSources.FederatedSlack]);
    const { result } = renderHook(() => useQueryController(), { wrapper });

    await act(async () => {
      await result.current.submit("federated query", jest.fn());
    });

    expect(mockSearchDocuments).toHaveBeenCalledTimes(2);
    const queriedSources = mockSearchDocuments.mock.calls.map(
      ([, options]) => options.filters.source_type[0]
    );
    expect(queriedSources).toEqual(
      expect.arrayContaining([ValidSources.Jira, ValidSources.FederatedSlack])
    );
  });

  it("merges per-source results sorted by score and records a per-source count", async () => {
    setConnectedSources([ValidSources.Jira, ValidSources.GoogleDrive]);
    mockSearchDocuments.mockImplementation((_query, options) => {
      const source = options.filters.source_type[0] as ValidSources;
      if (source === ValidSources.Jira) {
        return Promise.resolve({
          all_executed_queries: [],
          search_docs: [
            makeDoc("jira-1", ValidSources.Jira, 0.4),
            makeDoc("jira-2", ValidSources.Jira, 0.9),
          ],
          llm_selected_doc_ids: null,
          error: null,
        });
      }
      return Promise.resolve({
        all_executed_queries: [],
        search_docs: [makeDoc("gdrive-1", ValidSources.GoogleDrive, 0.7)],
        llm_selected_doc_ids: null,
        error: null,
      });
    });

    const { result } = renderHook(() => useQueryController(), { wrapper });

    await act(async () => {
      await result.current.submit("merge query", jest.fn());
    });

    expect(result.current.searchResults.map((d) => d.document_id)).toEqual([
      "jira-2",
      "gdrive-1",
      "jira-1",
    ]);
    expect(result.current.sourceCounts).toEqual({
      [ValidSources.Jira]: 2,
      [ValidSources.GoogleDrive]: 1,
    });
  });

  it("surfaces an error if any per-source search fails", async () => {
    setConnectedSources([ValidSources.Jira, ValidSources.GoogleDrive]);
    mockSearchDocuments.mockImplementation((_query, options) => {
      const source = options.filters.source_type[0] as ValidSources;
      if (source === ValidSources.Jira) {
        return Promise.resolve({
          all_executed_queries: [],
          search_docs: [],
          llm_selected_doc_ids: null,
          error: "Jira search failed",
        });
      }
      return emptySearchResponse();
    });

    const { result } = renderHook(() => useQueryController(), { wrapper });

    await act(async () => {
      await result.current.submit("erroring query", jest.fn());
    });

    expect(result.current.error).toBe("Jira search failed");
    expect(result.current.searchResults).toEqual([]);
    expect(result.current.sourceCounts).toEqual({});
  });

  it("selecting/clearing a source filter is client-side only and never re-queries", async () => {
    setConnectedSources([ValidSources.Jira, ValidSources.GoogleDrive]);
    const { result } = renderHook(() => useQueryController(), { wrapper });

    await act(async () => {
      await result.current.submit("client filter query", jest.fn());
    });
    mockSearchDocuments.mockClear();

    act(() => {
      result.current.applySourceFilter([ValidSources.Jira]);
    });
    expect(result.current.sourceFilter).toEqual([ValidSources.Jira]);

    act(() => {
      result.current.applySourceFilter([]);
    });
    expect(result.current.sourceFilter).toEqual([]);

    expect(mockSearchDocuments).not.toHaveBeenCalled();
  });

  it("refineSearch (e.g. a time-filter change) re-runs the full fan-out across every source", async () => {
    setConnectedSources([ValidSources.Jira, ValidSources.GoogleDrive]);
    const { result } = renderHook(() => useQueryController(), { wrapper });

    await act(async () => {
      await result.current.submit("initial query", jest.fn());
    });
    mockSearchDocuments.mockClear();

    await act(async () => {
      await result.current.refineSearch({ time_cutoff: "2026-01-01T00:00:00Z" });
    });

    expect(mockSearchDocuments).toHaveBeenCalledTimes(2);
    for (const [, options] of mockSearchDocuments.mock.calls) {
      expect(options.filters.time_cutoff).toBe("2026-01-01T00:00:00Z");
    }
  });

  it("resets sourceFilter and sourceCounts to empty on session change", async () => {
    setConnectedSources([ValidSources.Jira, ValidSources.GoogleDrive]);
    const { result } = renderHook(() => useQueryController(), { wrapper });

    act(() => {
      result.current.applySourceFilter([ValidSources.Jira]);
    });
    await act(async () => {
      await result.current.submit("pre-reset query", jest.fn());
    });
    expect(result.current.sourceCounts).not.toEqual({});

    act(() => {
      result.current.reset();
    });

    expect(result.current.sourceFilter).toEqual([]);
    expect(result.current.sourceCounts).toEqual({});
  });
});
