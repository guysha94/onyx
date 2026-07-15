"use client";

import { useCallback, useEffect, useRef, useState, useMemo } from "react";
import {
  BaseFilters,
  SearchDocWithContent,
  SearchFlowClassificationResponse,
  SearchFullResponse,
} from "@/lib/search/interfaces";
import { classifyQuery, searchDocuments } from "@/ee/lib/search/svc";
import useAppFocus from "@/hooks/useAppFocus";
import useCCPairs from "@/hooks/useCCPairs";
import { useFederatedConnectors } from "@/lib/hooks";
import { useTierAtLeast } from "@/hooks/useTierAtLeast";
import { Tier } from "@/lib/settings/types";
import { useIsSearchModeAvailable } from "@/lib/settings/hooks";
import { useUser } from "@/providers/UserProvider";
import { ValidSources } from "@/lib/types";
import {
  QueryControllerContext,
  QueryControllerValue,
  QueryState,
  AppMode,
} from "@/providers/QueryControllerProvider";

interface QueryControllerProviderProps {
  children: React.ReactNode;
}

export function QueryControllerProvider({
  children,
}: QueryControllerProviderProps) {
  const appFocus = useAppFocus();
  const businessTier = useTierAtLeast(Tier.BUSINESS);
  const searchUiEnabled = useIsSearchModeAvailable();
  const { user } = useUser();

  // ── Merged query state (discriminated union) ──────────────────────────
  const [state, setState] = useState<QueryState>({
    phase: "idle",
    appMode: "chat",
  });

  // Persistent app-mode preference — survives phase transitions and is
  // used to restore the correct mode when resetting back to idle.
  const appModeRef = useRef<AppMode>("chat");

  // ── App mode sync from user preferences ───────────────────────────────
  const persistedMode = user?.preferences?.default_app_mode;

  useEffect(() => {
    let mode: AppMode = "chat";
    if (businessTier && searchUiEnabled && persistedMode) {
      const lower = persistedMode.toLowerCase();
      mode = (["auto", "search", "chat"] as const).includes(lower as AppMode)
        ? (lower as AppMode)
        : "chat";
    }
    appModeRef.current = mode;
    setState((prev) =>
      prev.phase === "idle" ? { phase: "idle", appMode: mode } : prev
    );
  }, [businessTier, searchUiEnabled, persistedMode]);

  const setAppMode = useCallback(
    (mode: AppMode) => {
      if (!businessTier || !searchUiEnabled) return;
      setState((prev) => {
        if (prev.phase !== "idle") return prev;
        appModeRef.current = mode;
        return { phase: "idle", appMode: mode };
      });
    },
    [businessTier, searchUiEnabled]
  );

  // ── Ancillary state ───────────────────────────────────────────────────
  const [query, setQuery] = useState<string | null>(null);
  const [searchResults, setSearchResults] = useState<SearchDocWithContent[]>(
    []
  );
  const [sourceCounts, setSourceCounts] = useState<Record<string, number>>(
    {}
  );
  const [llmSelectedDocIds, setLlmSelectedDocIds] = useState<string[] | null>(
    null
  );
  const [error, setError] = useState<string | null>(null);

  // ── Search-mode source filter (session-scoped, display-only) ───────────
  // `sourceFilter` only controls which sources are *displayed* — it is
  // applied client-side in SearchUI. It no longer affects what gets fetched:
  // every search always fans out across every connected source (see
  // `availableSourcesRef` below) so that `sourceCounts` stays accurate for
  // every source regardless of the current selection.
  const [sourceFilter, setSourceFilter] = useState<ValidSources[]>([]);
  const applySourceFilter = useCallback((next: ValidSources[]) => {
    setSourceFilter(next);
  }, []);

  // ── Connected sources (for fan-out) ─────────────────────────────────────
  // Every search queries each of these sources independently so that
  // `sourceCounts` is always populated for every source, not just the
  // currently selected one(s). Mirrors the source list AppPage computes for
  // the picker's display metadata (regular CC pairs + federated connectors).
  const { ccPairs } = useCCPairs();
  const { data: federatedConnectorsData } = useFederatedConnectors();
  const availableSources = useMemo<ValidSources[]>(() => {
    const regular = ccPairs.map((pair) => pair.source);
    const federated = (federatedConnectorsData ?? []).map(
      (connector) => connector.source
    );
    return Array.from(new Set([...regular, ...federated]));
  }, [ccPairs, federatedConnectorsData]);
  // Synchronous mirror so `performSearch` (a stable useCallback) always reads
  // the latest connected sources without needing to be recreated on change.
  const availableSourcesRef = useRef<ValidSources[]>([]);
  useEffect(() => {
    availableSourcesRef.current = availableSources;
  }, [availableSources]);

  // Abort controllers for in-flight requests
  const classifyAbortRef = useRef<AbortController | null>(null);
  const searchAbortRef = useRef<AbortController | null>(null);

  /**
   * Perform document search (pure data-fetching, no phase side effects).
   *
   * Fans out one request per connected source in parallel (ignoring any
   * `source_type` on `filters` — source scoping is applied client-side in
   * SearchUI), then merges the results by score and records each source's
   * own hit count in `sourceCounts`. This guarantees every source's count is
   * always accurate, independent of which source(s) are currently selected
   * for display, and that "all sources" actually surfaces every source
   * instead of letting one source's stronger keyword/semantic scores crowd
   * out the rest of a single pooled top-N.
   *
   * Falls back to a single unscoped search when there are no connected
   * sources yet (e.g. a fresh deployment with no connectors configured).
   */
  const performSearch = useCallback(
    async (searchQuery: string, filters?: BaseFilters): Promise<void> => {
      if (searchAbortRef.current) {
        searchAbortRef.current.abort();
      }

      const controller = new AbortController();
      searchAbortRef.current = controller;

      const baseFilters: BaseFilters = {
        time_cutoff: filters?.time_cutoff ?? null,
        tags: filters?.tags ?? null,
        document_set: filters?.document_set ?? null,
        source_type: null,
      };

      const sourcesToQuery = availableSourcesRef.current;

      try {
        if (sourcesToQuery.length === 0) {
          const response: SearchFullResponse = await searchDocuments(
            searchQuery,
            {
              filters: baseFilters,
              numHits: 30,
              includeContent: false,
              signal: controller.signal,
            }
          );

          if (response.error) {
            setError(response.error);
            setSearchResults([]);
            setSourceCounts({});
            setLlmSelectedDocIds(null);
            return;
          }

          setError(null);
          setSearchResults(response.search_docs);
          setSourceCounts({});
          setLlmSelectedDocIds(response.llm_selected_doc_ids ?? null);
          return;
        }

        const responses = await Promise.all(
          sourcesToQuery.map((source) =>
            searchDocuments(searchQuery, {
              filters: { ...baseFilters, source_type: [source] },
              numHits: 30,
              includeContent: false,
              signal: controller.signal,
            })
          )
        );

        const firstError = responses.find((r) => r.error)?.error;
        if (firstError) {
          setError(firstError);
          setSearchResults([]);
          setSourceCounts({});
          setLlmSelectedDocIds(null);
          return;
        }

        const counts: Record<string, number> = {};
        const allDocs: SearchDocWithContent[] = [];
        let llmSelected: string[] | null = null;
        sourcesToQuery.forEach((source, i) => {
          const docs = responses[i]!.search_docs;
          counts[source] = docs.length;
          allDocs.push(...docs);
          const selectedForSource = responses[i]!.llm_selected_doc_ids;
          if (selectedForSource) {
            llmSelected = [...(llmSelected ?? []), ...selectedForSource];
          }
        });
        allDocs.sort((a, b) => (b.score ?? 0) - (a.score ?? 0));

        setError(null);
        setSearchResults(allDocs);
        setSourceCounts(counts);
        setLlmSelectedDocIds(llmSelected);
      } catch (err) {
        if (err instanceof Error && err.name === "AbortError") {
          throw err;
        }

        setError("Document search failed. Please try again.");
        setSearchResults([]);
        setSourceCounts({});
        setLlmSelectedDocIds(null);
      }
    },
    []
  );

  /**
   * Classify a query as search or chat
   */
  const performClassification = useCallback(
    async (classifyQueryText: string): Promise<"search" | "chat"> => {
      if (classifyAbortRef.current) {
        classifyAbortRef.current.abort();
      }

      const controller = new AbortController();
      classifyAbortRef.current = controller;

      try {
        const response: SearchFlowClassificationResponse = await classifyQuery(
          classifyQueryText,
          controller.signal
        );

        const result = response.is_search_flow ? "search" : "chat";
        return result;
      } catch (error) {
        if (error instanceof Error && error.name === "AbortError") {
          throw error;
        }

        setError("Query classification failed. Falling back to chat.");
        return "chat";
      }
    },
    []
  );

  /**
   * Submit a query - routes based on app mode
   */
  const submit = useCallback(
    async (
      submitQuery: string,
      onChat: (query: string) => void,
      filters?: BaseFilters
    ): Promise<void> => {
      setQuery(submitQuery);
      setError(null);

      const currentAppMode = appModeRef.current;

      // Always route through chat if:
      // 1. Not Enterprise Enabled
      // 2. Admin has disabled the Search UI
      // 3. Not in the "New Session" tab
      // 4. In "New Session" tab but app-mode is "Chat"
      if (
        !businessTier ||
        !searchUiEnabled ||
        !appFocus.isNewSession() ||
        currentAppMode === "chat"
      ) {
        setState({ phase: "chat" });
        setSearchResults([]);
        setSourceCounts({});
        setLlmSelectedDocIds(null);
        onChat(submitQuery);
        return;
      }

      // Search mode: immediately show SearchUI with loading state
      if (currentAppMode === "search") {
        setState({ phase: "searching" });
        try {
          await performSearch(submitQuery, filters);
        } catch (err) {
          if (err instanceof Error && err.name === "AbortError") return;
          throw err;
        }
        setState({ phase: "search-results" });
        return;
      }

      // Auto mode: classify first, then route
      setState({ phase: "classifying" });
      try {
        const result = await performClassification(submitQuery);

        if (result === "search") {
          setState({ phase: "searching" });
          await performSearch(submitQuery, filters);
          setState({ phase: "search-results" });
          appModeRef.current = "search";
        } else {
          setState({ phase: "chat" });
          setSearchResults([]);
          setSourceCounts({});
          setLlmSelectedDocIds(null);
          onChat(submitQuery);
        }
      } catch (error) {
        if (error instanceof Error && error.name === "AbortError") {
          return;
        }

        setState({ phase: "chat" });
        setSearchResults([]);
        setSourceCounts({});
        setLlmSelectedDocIds(null);
        onChat(submitQuery);
      }
    },
    [
      appFocus,
      performClassification,
      performSearch,
      businessTier,
      searchUiEnabled,
    ]
  );

  /**
   * Re-run the current search query with updated server-side filters
   */
  const refineSearch = useCallback(
    async (filters: BaseFilters): Promise<void> => {
      if (!query) return;
      setState({ phase: "searching" });
      try {
        await performSearch(query, filters);
      } catch (err) {
        if (err instanceof Error && err.name === "AbortError") return;
        throw err;
      }
      setState({ phase: "search-results" });
    },
    [query, performSearch]
  );

  /**
   * Reset all state to initial values
   */
  const reset = useCallback(() => {
    if (classifyAbortRef.current) {
      classifyAbortRef.current.abort();
      classifyAbortRef.current = null;
    }
    if (searchAbortRef.current) {
      searchAbortRef.current.abort();
      searchAbortRef.current = null;
    }

    setQuery(null);
    setState({ phase: "idle", appMode: appModeRef.current });
    setSearchResults([]);
    setSourceCounts({});
    setLlmSelectedDocIds(null);
    setError(null);
    // New session (or first mount) always starts scoped to all sources.
    applySourceFilter([]);
  }, [applySourceFilter]);

  const value: QueryControllerValue = useMemo(
    () => ({
      state,
      setAppMode,
      searchResults,
      sourceCounts,
      llmSelectedDocIds,
      error,
      sourceFilter,
      applySourceFilter,
      submit,
      refineSearch,
      reset,
    }),
    [
      state,
      setAppMode,
      searchResults,
      sourceCounts,
      llmSelectedDocIds,
      error,
      sourceFilter,
      applySourceFilter,
      submit,
      refineSearch,
      reset,
    ]
  );

  // Sync state with navigation context
  useEffect(reset, [appFocus, reset]);

  return (
    <QueryControllerContext.Provider value={value}>
      {children}
    </QueryControllerContext.Provider>
  );
}
