import React, { PropsWithChildren } from "react";
import { act, renderHook } from "@testing-library/react";
import { ValidSources } from "@/lib/types";
import {
  QueryControllerProvider,
  useQueryController,
} from "@/providers/QueryControllerProvider";

const mockSearchDocuments = jest.fn();

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

describe("EE QueryControllerProvider source filter", () => {
  beforeEach(() => {
    mockSearchDocuments.mockReset();
    mockSearchDocuments.mockImplementation(emptySearchResponse);
  });

  it("defaults to all sources (source_type: null) on a fresh bar-submit query", async () => {
    const { result } = renderHook(() => useQueryController(), { wrapper });

    await act(async () => {
      await result.current.submit("first query", jest.fn());
    });

    expect(mockSearchDocuments).toHaveBeenCalledTimes(1);
    const [, options] = mockSearchDocuments.mock.calls[0]!;
    expect(options.filters).toEqual({ source_type: null });
  });

  it("synthesizes the current selection from the ref on a new bar-submit query", async () => {
    const { result } = renderHook(() => useQueryController(), { wrapper });

    act(() => {
      result.current.applySourceFilter([ValidSources.Jira]);
    });
    expect(result.current.sourceFilter).toEqual([ValidSources.Jira]);

    await act(async () => {
      await result.current.submit("scoped query", jest.fn());
    });

    expect(mockSearchDocuments).toHaveBeenCalledTimes(1);
    const [, options] = mockSearchDocuments.mock.calls[0]!;
    expect(options.filters).toEqual({ source_type: [ValidSources.Jira] });
  });

  it("uses an explicit refine filters object verbatim for single/multi select", async () => {
    const { result } = renderHook(() => useQueryController(), { wrapper });

    // Establish a query so refineSearch has something to re-run.
    await act(async () => {
      await result.current.submit("multi select query", jest.fn());
    });
    mockSearchDocuments.mockClear();

    await act(async () => {
      await result.current.refineSearch({
        source_type: [ValidSources.Jira, ValidSources.GoogleDrive],
      });
    });

    expect(mockSearchDocuments).toHaveBeenCalledTimes(1);
    const [, options] = mockSearchDocuments.mock.calls[0]!;
    expect(options.filters).toEqual({
      source_type: [ValidSources.Jira, ValidSources.GoogleDrive],
    });
  });

  it("clears the scope when an explicit refine filters object sets source_type: null", async () => {
    const { result } = renderHook(() => useQueryController(), { wrapper });

    act(() => {
      result.current.applySourceFilter([ValidSources.Jira]);
    });

    await act(async () => {
      await result.current.submit("clear query", jest.fn());
    });
    mockSearchDocuments.mockClear();

    // Simulates SearchUI's Clear action: applySourceFilter([]) then an
    // explicit refine with source_type: null. The explicit object must win
    // outright — it must NOT fall back to the (still-populated at this
    // instant) ref via `??`, which was the pre-fix "clear" bug.
    act(() => {
      result.current.applySourceFilter([]);
    });
    await act(async () => {
      await result.current.refineSearch({ source_type: null });
    });

    expect(mockSearchDocuments).toHaveBeenCalledTimes(1);
    const [, options] = mockSearchDocuments.mock.calls[0]!;
    expect(options.filters).toEqual({ source_type: null });
    expect(result.current.sourceFilter).toEqual([]);
  });

  it("toggling a source off narrows an explicit refine to the remaining selection", async () => {
    const { result } = renderHook(() => useQueryController(), { wrapper });

    act(() => {
      result.current.applySourceFilter([
        ValidSources.Jira,
        ValidSources.GoogleDrive,
      ]);
    });
    await act(async () => {
      await result.current.submit("toggle query", jest.fn());
    });
    mockSearchDocuments.mockClear();

    // Toggling GoogleDrive off, mirroring SearchUI's handleSourceToggle.
    const next = [ValidSources.Jira];
    act(() => {
      result.current.applySourceFilter(next);
    });
    await act(async () => {
      await result.current.refineSearch({ source_type: next });
    });

    expect(mockSearchDocuments).toHaveBeenCalledTimes(1);
    const [, options] = mockSearchDocuments.mock.calls[0]!;
    expect(options.filters).toEqual({ source_type: [ValidSources.Jira] });
    expect(result.current.sourceFilter).toEqual([ValidSources.Jira]);
  });

  it("resets the selection to all-sources on session change", async () => {
    const { result } = renderHook(() => useQueryController(), { wrapper });

    act(() => {
      result.current.applySourceFilter([ValidSources.Jira]);
    });
    expect(result.current.sourceFilter).toEqual([ValidSources.Jira]);

    act(() => {
      result.current.reset();
    });
    expect(result.current.sourceFilter).toEqual([]);

    await act(async () => {
      await result.current.submit("post-reset query", jest.fn());
    });

    expect(mockSearchDocuments).toHaveBeenCalledTimes(1);
    const [, options] = mockSearchDocuments.mock.calls[0]!;
    expect(options.filters).toEqual({ source_type: null });
  });
});
