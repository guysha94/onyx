import React, { PropsWithChildren } from "react";
import { act, renderHook } from "@testing-library/react";
import { ValidSources } from "@/lib/types";
import { SearchDocWithContent } from "@/lib/search/interfaces";
import {
  QueryControllerProvider,
  useQueryController,
  DEFAULT_SEARCH_MAX_RESULTS,
  MIN_SEARCH_MAX_RESULTS,
  MAX_SEARCH_MAX_RESULTS,
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
    expect(options.numHits).toBe(DEFAULT_SEARCH_MAX_RESULTS);
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
    for (const [, options] of mockSearchDocuments.mock.calls) {
      expect(options.numHits).toBe(DEFAULT_SEARCH_MAX_RESULTS);
    }
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

describe("EE QueryControllerProvider max results", () => {
  beforeEach(() => {
    mockSearchDocuments.mockReset();
    mockSearchDocuments.mockImplementation(emptySearchResponse);
    setConnectedSources([]);
  });

  it("defaults maxResults to DEFAULT_SEARCH_MAX_RESULTS", () => {
    const { result } = renderHook(() => useQueryController(), { wrapper });
    expect(result.current.maxResults).toBe(DEFAULT_SEARCH_MAX_RESULTS);
  });

  it("applyMaxResults updates numHits sent to every per-source request", async () => {
    setConnectedSources([ValidSources.Jira, ValidSources.GoogleDrive]);
    const { result } = renderHook(() => useQueryController(), { wrapper });

    act(() => {
      result.current.applyMaxResults(5);
    });
    expect(result.current.maxResults).toBe(5);

    await act(async () => {
      await result.current.submit("capped query", jest.fn());
    });

    expect(mockSearchDocuments).toHaveBeenCalledTimes(2);
    for (const [, options] of mockSearchDocuments.mock.calls) {
      expect(options.numHits).toBe(5);
    }
  });

  it("slices the merged, score-sorted results to the global top-N even when sources return more combined", async () => {
    setConnectedSources([ValidSources.Jira, ValidSources.GoogleDrive]);
    mockSearchDocuments.mockImplementation((_query, options) => {
      const source = options.filters.source_type[0] as ValidSources;
      if (source === ValidSources.Jira) {
        return Promise.resolve({
          all_executed_queries: [],
          search_docs: [
            makeDoc("jira-1", ValidSources.Jira, 0.9),
            makeDoc("jira-2", ValidSources.Jira, 0.5),
          ],
          llm_selected_doc_ids: null,
          error: null,
        });
      }
      return Promise.resolve({
        all_executed_queries: [],
        search_docs: [
          makeDoc("gdrive-1", ValidSources.GoogleDrive, 0.8),
          makeDoc("gdrive-2", ValidSources.GoogleDrive, 0.3),
        ],
        llm_selected_doc_ids: null,
        error: null,
      });
    });

    const { result } = renderHook(() => useQueryController(), { wrapper });

    act(() => {
      result.current.applyMaxResults(2);
    });

    await act(async () => {
      await result.current.submit("global cap query", jest.fn());
    });

    // 4 docs returned combined, but only the top 2 by score should surface.
    expect(result.current.searchResults.map((d) => d.document_id)).toEqual([
      "jira-1",
      "gdrive-1",
    ]);
    // sourceCounts must reflect the capped, displayed set (not the raw 2+2
    // per-source responses), so it sums to the cap and matches what's shown
    // when a source is selected.
    expect(result.current.sourceCounts).toEqual({
      [ValidSources.Jira]: 1,
      [ValidSources.GoogleDrive]: 1,
    });
  });

  it("squeezes a lower-scoring source's count down to its surviving-doc count under the global cap", async () => {
    setConnectedSources([ValidSources.Jira, ValidSources.GoogleDrive]);
    mockSearchDocuments.mockImplementation((_query, options) => {
      const source = options.filters.source_type[0] as ValidSources;
      if (source === ValidSources.Jira) {
        // All of Jira's docs score higher than Google Drive's.
        return Promise.resolve({
          all_executed_queries: [],
          search_docs: [
            makeDoc("jira-1", ValidSources.Jira, 0.95),
            makeDoc("jira-2", ValidSources.Jira, 0.9),
            makeDoc("jira-3", ValidSources.Jira, 0.85),
          ],
          llm_selected_doc_ids: null,
          error: null,
        });
      }
      return Promise.resolve({
        all_executed_queries: [],
        search_docs: [
          makeDoc("gdrive-1", ValidSources.GoogleDrive, 0.5),
          makeDoc("gdrive-2", ValidSources.GoogleDrive, 0.4),
        ],
        llm_selected_doc_ids: null,
        error: null,
      });
    });

    const { result } = renderHook(() => useQueryController(), { wrapper });

    act(() => {
      result.current.applyMaxResults(4);
    });

    await act(async () => {
      await result.current.submit("squeeze query", jest.fn());
    });

    // Raw per-source hits are 3 + 2 = 5, but the global cap of 4 squeezes
    // Google Drive down to just 1 surviving doc (gdrive-1).
    expect(result.current.searchResults).toHaveLength(4);
    expect(result.current.sourceCounts).toEqual({
      [ValidSources.Jira]: 3,
      [ValidSources.GoogleDrive]: 1,
    });
    // The counts must sum to the actual number of displayed results, not the
    // raw per-source total (5).
    expect(
      Object.values(result.current.sourceCounts).reduce((a, b) => a + b, 0)
    ).toBe(result.current.searchResults.length);
  });

  it("clamps out-of-range values to MIN/MAX_SEARCH_MAX_RESULTS", () => {
    const { result } = renderHook(() => useQueryController(), { wrapper });

    act(() => {
      result.current.applyMaxResults(0);
    });
    expect(result.current.maxResults).toBe(MIN_SEARCH_MAX_RESULTS);

    act(() => {
      result.current.applyMaxResults(-50);
    });
    expect(result.current.maxResults).toBe(MIN_SEARCH_MAX_RESULTS);

    act(() => {
      result.current.applyMaxResults(9999);
    });
    expect(result.current.maxResults).toBe(MAX_SEARCH_MAX_RESULTS);
  });

  it("falls back to the default for non-finite input", () => {
    const { result } = renderHook(() => useQueryController(), { wrapper });

    act(() => {
      result.current.applyMaxResults(42);
    });
    expect(result.current.maxResults).toBe(42);

    act(() => {
      result.current.applyMaxResults(NaN);
    });
    expect(result.current.maxResults).toBe(DEFAULT_SEARCH_MAX_RESULTS);
  });

  it("resets maxResults to the default on session change", () => {
    const { result } = renderHook(() => useQueryController(), { wrapper });

    act(() => {
      result.current.applyMaxResults(7);
    });
    expect(result.current.maxResults).toBe(7);

    act(() => {
      result.current.reset();
    });

    expect(result.current.maxResults).toBe(DEFAULT_SEARCH_MAX_RESULTS);
  });
});
