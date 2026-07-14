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
  const [llmSelectedDocIds, setLlmSelectedDocIds] = useState<string[] | null>(
    null
  );
  const [error, setError] = useState<string | null>(null);

  // ── Search-mode source filter (session-scoped) ─────────────────────────
  // `sourceFilter` is the canonical, render-visible selection. `sourceFilterRef`
  // is a synchronous mirror used only so `submit()` (the bar-query path, which
  // is called with no explicit filters) can read the *current* selection
  // without waiting for a state update to flush. `applySourceFilter` is the
  // ONLY place that writes either of them — never call `setSourceFilter`
  // directly anywhere else, or the ref and state can desync.
  const [sourceFilter, setSourceFilter] = useState<ValidSources[]>([]);
  const sourceFilterRef = useRef<ValidSources[]>([]);
  const applySourceFilter = useCallback((next: ValidSources[]) => {
    sourceFilterRef.current = next;
    setSourceFilter(next);
  }, []);

  // Abort controllers for in-flight requests
  const classifyAbortRef = useRef<AbortController | null>(null);
  const searchAbortRef = useRef<AbortController | null>(null);

  /**
   * Perform document search (pure data-fetching, no phase side effects)
   */
  const performSearch = useCallback(
    async (searchQuery: string, filters?: BaseFilters): Promise<void> => {
      if (searchAbortRef.current) {
        searchAbortRef.current.abort();
      }

      const controller = new AbortController();
      searchAbortRef.current = controller;

      // Scope resolution happens at the whole-object level, never by merging
      // individual fields:
      // - Refine path (caller passed a `filters` object, e.g. from SearchUI's
      //   buildFilters()): use it verbatim, including `source_type: null` on
      //   an explicit clear — that must actually clear the scope.
      // - Bar-submit path (no `filters` object, i.e. a brand-new query typed
      //   in the input bar): synthesize the scope from the current session
      //   selection via the synchronous ref.
      const effectiveFilters: BaseFilters =
        filters ??
        (sourceFilterRef.current.length > 0
          ? { source_type: sourceFilterRef.current }
          : { source_type: null });

      try {
        const response: SearchFullResponse = await searchDocuments(
          searchQuery,
          {
            filters: effectiveFilters,
            numHits: 30,
            includeContent: false,
            signal: controller.signal,
          }
        );

        if (response.error) {
          setError(response.error);
          setSearchResults([]);
          setLlmSelectedDocIds(null);
          return;
        }

        setError(null);
        setSearchResults(response.search_docs);
        setLlmSelectedDocIds(response.llm_selected_doc_ids ?? null);
      } catch (err) {
        if (err instanceof Error && err.name === "AbortError") {
          throw err;
        }

        setError("Document search failed. Please try again.");
        setSearchResults([]);
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
          setLlmSelectedDocIds(null);
          onChat(submitQuery);
        }
      } catch (error) {
        if (error instanceof Error && error.name === "AbortError") {
          return;
        }

        setState({ phase: "chat" });
        setSearchResults([]);
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
