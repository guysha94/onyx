"use client";

import { createContext, useContext } from "react";
import { paidTierGated } from "@/ce";
import { QueryControllerProvider as EEQueryControllerProvider } from "@/ee/providers/QueryControllerProvider";
import { SearchDocWithContent, BaseFilters } from "@/lib/search/interfaces";
import { ValidSources } from "@/lib/types";

export type AppMode = "auto" | "search" | "chat";

/**
 * Single source of truth for Search mode's max-results bounds. Import these
 * everywhere a default/min/max is needed (EE provider, SearchUI) instead of
 * repeating the literals, so bumping the default only requires one edit.
 */
export const DEFAULT_SEARCH_MAX_RESULTS = 30;
export const MIN_SEARCH_MAX_RESULTS = 1;
export const MAX_SEARCH_MAX_RESULTS = 500;

export type QueryState =
  | { phase: "idle"; appMode: AppMode }
  | { phase: "classifying" }
  | { phase: "searching" }
  | { phase: "search-results" }
  | { phase: "chat" };

export interface QueryControllerValue {
  /** Single state variable encoding both the query lifecycle phase and (when idle) the user's mode selection. */
  state: QueryState;
  /** Update the app mode. Only takes effect when idle. No-op in CE or when search is unavailable. */
  setAppMode: (mode: AppMode) => void;
  /** Search results (empty if chat or not yet searched) */
  searchResults: SearchDocWithContent[];
  /**
   * Result count per source (keyed by `ValidSources`) for the current query,
   * always covering every connected source regardless of `sourceFilter` —
   * used to populate the counts shown in the source picker.
   */
  sourceCounts: Record<string, number>;
  /** Document IDs selected by the LLM as most relevant */
  llmSelectedDocIds: string[] | null;
  /** User-facing error message from the last search or classification request, null when idle */
  error: string | null;
  /**
   * Session-scoped Search-mode source selection. Empty array means "all sources".
   * Purely a display-side scope: it filters which of the already-fetched,
   * always-all-sources `searchResults` are shown, and never triggers a new
   * server request. Persists across new queries within the same session;
   * reset to empty on session change (see `reset`). This is the single
   * source of truth for the selection — always read this, never track a
   * separate copy in a component.
   */
  sourceFilter: ValidSources[];
  /** The only way to change `sourceFilter`. */
  applySourceFilter: (next: ValidSources[]) => void;
  /**
   * Session-scoped Search-mode max-results cap, clamped to
   * `[MIN_SEARCH_MAX_RESULTS, MAX_SEARCH_MAX_RESULTS]`. Applied as the
   * `numHits` for every per-source search request and as a final slice on
   * the merged, score-sorted result list. Defaults to
   * `DEFAULT_SEARCH_MAX_RESULTS`; reset to it on session change.
   */
  maxResults: number;
  /**
   * The only way to change `maxResults`. Clamps out-of-range/invalid input
   * rather than rejecting it.
   */
  applyMaxResults: (next: number) => void;
  /** Submit a query - routes to search or chat based on app mode */
  submit: (
    query: string,
    onChat: (query: string) => void,
    filters?: BaseFilters
  ) => Promise<void>;
  /**
   * Re-run the current search query with updated server-side filters (time
   * cutoff / tags). Always re-fans-out across every connected source; any
   * `source_type` on `filters` is ignored since source scoping is applied
   * client-side via `sourceFilter`.
   */
  refineSearch: (filters: BaseFilters) => Promise<void>;
  /** Reset all state to initial values */
  reset: () => void;
}

export const QueryControllerContext = createContext<QueryControllerValue>({
  state: { phase: "idle", appMode: "chat" },
  setAppMode: () => undefined,
  searchResults: [],
  sourceCounts: {},
  llmSelectedDocIds: null,
  error: null,
  sourceFilter: [],
  applySourceFilter: () => undefined,
  maxResults: DEFAULT_SEARCH_MAX_RESULTS,
  applyMaxResults: () => undefined,
  submit: async (_q, onChat) => {
    onChat(_q);
  },
  refineSearch: async () => undefined,
  reset: () => undefined,
});

export function useQueryController(): QueryControllerValue {
  return useContext(QueryControllerContext);
}

export const QueryControllerProvider = paidTierGated(EEQueryControllerProvider);
