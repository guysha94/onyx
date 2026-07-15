"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  BaseFilters,
  MinimalOnyxDocument,
  SourceMetadata,
} from "@/lib/search/interfaces";
import SearchCard from "@/ee/sections/SearchCard";
import { Divider, Pagination } from "@opal/components";
import { EmptyMessageCard } from "@opal/components";
import { IllustrationContent } from "@opal/layouts";
import SvgNoResult from "@opal/illustrations/no-result";
import { Tag, ValidSources } from "@/lib/types";
import { getTimeFilterDate, TimeFilter } from "@opal/time";
import useTags from "@/hooks/useTags";
import { SourceIcon } from "@/components/SourceIcon";
import Text from "@/refresh-components/texts/Text";
import { Section } from "@/layouts/general-layouts";
import { Popover, PopoverMenu } from "@opal/components";
import {
  SvgCheck,
  SvgClock,
  SvgTag,
  SvgPlug,
  SvgHash,
  SvgSimpleLoader,
} from "@opal/icons";
import { FilterButton } from "@opal/components";
import { InputTypeIn } from "@opal/components";
import InputNumber from "@/refresh-components/inputs/InputNumber";
import useFilter from "@/hooks/useFilter";
import { LineItemButton } from "@opal/components";
import {
  useQueryController,
  DEFAULT_SEARCH_MAX_RESULTS,
  MIN_SEARCH_MAX_RESULTS,
  MAX_SEARCH_MAX_RESULTS,
} from "@/providers/QueryControllerProvider";
import { cn } from "@opal/utils";
import { toast } from "@/hooks/useToast";

// ============================================================================
// Types
// ============================================================================

export interface SearchResultsProps {
  /** Callback when a document is clicked */
  onDocumentClick: (doc: MinimalOnyxDocument) => void;
  /** All configured connector sources, used to populate the Sources filter */
  sources: SourceMetadata[];
}

// ============================================================================
// Constants
// ============================================================================

const RESULTS_PER_PAGE = 20;

const TIME_FILTER_OPTIONS: { value: TimeFilter; label: string }[] = [
  { value: "day", label: "Past 24 hours" },
  { value: "week", label: "Past week" },
  { value: "month", label: "Past month" },
  { value: "year", label: "Past year" },
];

export default function SearchUI({
  onDocumentClick,
  sources,
}: SearchResultsProps) {
  // Available tags from backend
  const { tags: availableTags } = useTags();
  const {
    state,
    searchResults: results,
    sourceCounts,
    llmSelectedDocIds,
    error,
    sourceFilter,
    applySourceFilter,
    maxResults,
    applyMaxResults,
    refineSearch: onRefineSearch,
  } = useQueryController();

  const prevErrorRef = useRef<string | null>(null);

  // Show a toast notification when a new error occurs
  useEffect(() => {
    if (error && error !== prevErrorRef.current) {
      toast.error(error);
    }
    prevErrorRef.current = error;
  }, [error]);

  // Filter state
  const [timeFilter, setTimeFilter] = useState<TimeFilter | null>(null);
  const [timeFilterOpen, setTimeFilterOpen] = useState(false);
  const [selectedTags, setSelectedTags] = useState<Tag[]>([]);
  const [tagFilterOpen, setTagFilterOpen] = useState(false);
  const [sourceFilterOpen, setSourceFilterOpen] = useState(false);
  const [maxResultsFilterOpen, setMaxResultsFilterOpen] = useState(false);
  // Snapshot of `maxResults` taken when the popover opens, so we only
  // re-query on close if the value actually changed (avoids a re-query per
  // keystroke/step while the popover is open).
  const maxResultsOnOpenRef = useRef<number>(maxResults);

  // Pagination state
  const [currentPage, setCurrentPage] = useState(1);

  const tagExtractor = useCallback(
    (tag: Tag) => `${tag.tag_key} ${tag.tag_value}`,
    []
  );
  const {
    query: tagQuery,
    setQuery: setTagQuery,
    filtered: filteredTags,
  } = useFilter(availableTags, tagExtractor);

  const sourceExtractor = useCallback(
    (source: SourceMetadata) => source.displayName,
    []
  );
  const {
    query: sourceQuery,
    setQuery: setSourceQuery,
    filtered: filteredSources,
  } = useFilter(sources, sourceExtractor);

  // Build the server-side (time/tag) filters from current state. Source
  // scoping is NOT included here — it's applied client-side below against
  // the always-unscoped `results`, so that every source's count stays
  // accurate no matter which source(s) are selected for display.
  const buildFilters = (
    overrides: {
      time?: TimeFilter | null;
      tags?: Tag[];
    } = {}
  ): BaseFilters => {
    const time = overrides.time !== undefined ? overrides.time : timeFilter;
    const tags = overrides.tags !== undefined ? overrides.tags : selectedTags;
    const cutoff = time ? getTimeFilterDate(time) : null;
    return {
      time_cutoff: cutoff?.toISOString() ?? null,
      tags:
        tags.length > 0
          ? tags.map((t) => ({ tag_key: t.tag_key, tag_value: t.tag_value }))
          : null,
    };
  };

  // Reset pagination when results change
  useEffect(() => {
    setCurrentPage(1);
  }, [results]);

  // Create a set for fast lookup of LLM-selected docs
  const llmSelectedSet = new Set(llmSelectedDocIds ?? []);

  // Filter by the selected source(s) (client-side — `results` always covers
  // every connected source, see QueryControllerProvider), then sort:
  // LLM-selected first, then by score.
  const filteredAndSortedResults = useMemo(() => {
    const scoped =
      sourceFilter.length > 0
        ? results.filter((doc) => sourceFilter.includes(doc.source_type))
        : results;

    return [...scoped].sort((a, b) => {
      const aSelected = llmSelectedSet.has(a.document_id);
      const bSelected = llmSelectedSet.has(b.document_id);

      if (aSelected && !bSelected) return -1;
      if (!aSelected && bSelected) return 1;

      return (b.score ?? 0) - (a.score ?? 0);
    });
  }, [results, sourceFilter, llmSelectedSet]);

  // Pagination
  const totalPages = Math.max(
    1,
    Math.ceil(filteredAndSortedResults.length / RESULTS_PER_PAGE)
  );
  const paginatedResults = useMemo(() => {
    const start = (currentPage - 1) * RESULTS_PER_PAGE;
    return filteredAndSortedResults.slice(start, start + RESULTS_PER_PAGE);
  }, [filteredAndSortedResults, currentPage]);

  // Selecting/deselecting a source only changes what's *displayed* — it
  // never re-queries the server, so every source's count (from
  // `sourceCounts`, always covering every connected source) never changes
  // as a result of the current selection.
  const handleSourceToggle = (source: ValidSources) => {
    const next = sourceFilter.includes(source)
      ? sourceFilter.filter((s) => s !== source)
      : [...sourceFilter, source];
    applySourceFilter(next);
    setCurrentPage(1);
  };

  const handleSourceClear = () => {
    applySourceFilter([]);
    setCurrentPage(1);
    setSourceFilterOpen(false);
  };

  // Unlike source selection, `maxResults` is a real query parameter (it sets
  // `numHits`), so a change must re-run the search. We only do this on
  // popover close (not on every keystroke/step) by comparing against the
  // value captured when the popover opened.
  const handleMaxResultsOpenChange = (open: boolean) => {
    if (open) {
      maxResultsOnOpenRef.current = maxResults;
    } else if (maxResults !== maxResultsOnOpenRef.current) {
      setCurrentPage(1);
      onRefineSearch(buildFilters());
    }
    setMaxResultsFilterOpen(open);
  };

  const handleMaxResultsClear = () => {
    applyMaxResults(DEFAULT_SEARCH_MAX_RESULTS);
    setCurrentPage(1);
    setMaxResultsFilterOpen(false);
    onRefineSearch(buildFilters());
  };

  const showEmpty = !error && filteredAndSortedResults.length === 0;

  // Show a centered spinner while search is in-flight (after all hooks)
  if (state.phase === "searching") {
    return (
      <div className="flex-1 min-h-0 w-full flex items-center justify-center">
        <SvgSimpleLoader />
      </div>
    );
  }

  return (
    <div className="flex-1 min-h-0 w-full flex flex-col gap-3">
      {/* ── Top row: Filters + Result count ── */}
      <div className="shrink-0 flex flex-row gap-x-4">
        <div
          className={cn(
            "flex flex-col justify-end gap-3",
            showEmpty ? "flex-1" : "flex-3"
          )}
        >
          <div className="flex flex-row gap-2">
            {/* Time filter */}
            <Popover open={timeFilterOpen} onOpenChange={setTimeFilterOpen}>
              <Popover.Trigger asChild>
                <FilterButton
                  icon={SvgClock}
                  active={!!timeFilter}
                  onClear={() => {
                    setTimeFilter(null);
                    onRefineSearch(buildFilters({ time: null }));
                  }}
                >
                  {TIME_FILTER_OPTIONS.find((o) => o.value === timeFilter)
                    ?.label ?? "All Time"}
                </FilterButton>
              </Popover.Trigger>
              <Popover.Content align="start" width="md">
                <PopoverMenu>
                  {TIME_FILTER_OPTIONS.map((opt) => (
                    <LineItemButton
                      key={opt.value}
                      onClick={() => {
                        setTimeFilter(opt.value);
                        setTimeFilterOpen(false);
                        onRefineSearch(buildFilters({ time: opt.value }));
                      }}
                      state={timeFilter === opt.value ? "selected" : "empty"}
                      icon={timeFilter === opt.value ? SvgCheck : SvgClock}
                      title={opt.label}
                      sizePreset="main-ui"
                      variant="section"
                    />
                  ))}
                </PopoverMenu>
              </Popover.Content>
            </Popover>

            {/* Tag filter */}
            <Popover open={tagFilterOpen} onOpenChange={setTagFilterOpen}>
              <Popover.Trigger asChild>
                <FilterButton
                  icon={SvgTag}
                  active={selectedTags.length > 0}
                  onClear={() => {
                    setSelectedTags([]);
                    onRefineSearch(buildFilters({ tags: [] }));
                  }}
                >
                  {selectedTags.length > 0
                    ? `${selectedTags.length} Tag${
                        selectedTags.length > 1 ? "s" : ""
                      }`
                    : "Tags"}
                </FilterButton>
              </Popover.Trigger>
              <Popover.Content align="start" width="lg">
                <PopoverMenu>
                  <InputTypeIn
                    searchIcon
                    placeholder="Filter tags..."
                    value={tagQuery}
                    onChange={(e) => setTagQuery(e.target.value)}
                    clearButton
                    variant="internal"
                  />
                  {filteredTags.map((tag) => {
                    const isSelected = selectedTags.some(
                      (t) =>
                        t.tag_key === tag.tag_key &&
                        t.tag_value === tag.tag_value
                    );
                    return (
                      <LineItemButton
                        key={`${tag.tag_key}=${tag.tag_value}`}
                        onClick={() => {
                          const next = isSelected
                            ? selectedTags.filter(
                                (t) =>
                                  t.tag_key !== tag.tag_key ||
                                  t.tag_value !== tag.tag_value
                              )
                            : [...selectedTags, tag];
                          setSelectedTags(next);
                          onRefineSearch(buildFilters({ tags: next }));
                        }}
                        state={isSelected ? "selected" : "empty"}
                        icon={isSelected ? SvgCheck : SvgTag}
                        title={tag.tag_value}
                        sizePreset="main-ui"
                        variant="section"
                      />
                    );
                  })}
                </PopoverMenu>
              </Popover.Content>
            </Popover>

            {/* Source filter */}
            <Popover open={sourceFilterOpen} onOpenChange={setSourceFilterOpen}>
              <Popover.Trigger asChild>
                <FilterButton
                  icon={SvgPlug}
                  active={sourceFilter.length > 0}
                  onClear={handleSourceClear}
                  data-testid="source-filter-trigger"
                >
                  {sourceFilter.length > 0
                    ? `${sourceFilter.length} Source${
                        sourceFilter.length > 1 ? "s" : ""
                      }`
                    : "Sources"}
                </FilterButton>
              </Popover.Trigger>
              <Popover.Content align="start" width="lg">
                <PopoverMenu>
                  <LineItemButton
                    onClick={handleSourceClear}
                    state={sourceFilter.length === 0 ? "selected" : "empty"}
                    icon={sourceFilter.length === 0 ? SvgCheck : SvgPlug}
                    title="All Sources"
                    sizePreset="main-ui"
                    variant="section"
                  />
                  <Divider paddingParallel="fit" paddingPerpendicular="fit" />
                  <InputTypeIn
                    searchIcon
                    placeholder="Filter sources..."
                    value={sourceQuery}
                    onChange={(e) => setSourceQuery(e.target.value)}
                    clearButton
                    variant="internal"
                  />
                  {filteredSources.map((source) => {
                    const isSelected = sourceFilter.includes(
                      source.internalName
                    );
                    const count = sourceCounts[source.internalName] ?? 0;
                    return (
                      <LineItemButton
                        key={source.internalName}
                        icon={(props) => (
                          <SourceIcon
                            sourceType={source.internalName}
                            iconSize={16}
                            {...props}
                          />
                        )}
                        onClick={() => handleSourceToggle(source.internalName)}
                        state={isSelected ? "selected" : "empty"}
                        title={source.displayName}
                        selectVariant="select-heavy"
                        sizePreset="main-ui"
                        variant="section"
                        rightChildren={<Text text03>{count}</Text>}
                      />
                    );
                  })}
                </PopoverMenu>
              </Popover.Content>
            </Popover>

            {/* Max results filter */}
            <Popover
              open={maxResultsFilterOpen}
              onOpenChange={handleMaxResultsOpenChange}
            >
              <Popover.Trigger asChild>
                <FilterButton
                  icon={SvgHash}
                  active={maxResults !== DEFAULT_SEARCH_MAX_RESULTS}
                  onClear={handleMaxResultsClear}
                  data-testid="max-results-filter-trigger"
                >
                  {`Max: ${maxResults}`}
                </FilterButton>
              </Popover.Trigger>
              <Popover.Content align="start" width="md">
                <PopoverMenu>
                  {[
                    <InputNumber
                      key="max-results-input"
                      value={maxResults}
                      onChange={(next) =>
                        applyMaxResults(next ?? DEFAULT_SEARCH_MAX_RESULTS)
                      }
                      min={MIN_SEARCH_MAX_RESULTS}
                      max={MAX_SEARCH_MAX_RESULTS}
                      defaultValue={DEFAULT_SEARCH_MAX_RESULTS}
                      showReset
                    />,
                  ]}
                </PopoverMenu>
              </Popover.Content>
            </Popover>
          </div>

          <Divider paddingParallel="fit" paddingPerpendicular="fit" />
        </div>

        {!showEmpty && (
          <div className="flex-1 flex flex-col justify-end gap-3">
            <Section alignItems="start">
              <Text text03 mainUiMuted>
                {filteredAndSortedResults.length} Results
              </Text>
            </Section>

            <Divider paddingParallel="fit" paddingPerpendicular="fit" />
          </div>
        )}
      </div>

      {/* ── Middle row: Results ── */}
      <div
        className={cn(
          "flex-1 min-h-0 overflow-y-scroll flex flex-col gap-2",
          showEmpty && "justify-center"
        )}
      >
        {error ? (
          <EmptyMessageCard
            sizePreset="main-ui"
            title="Search failed"
            description={error}
          />
        ) : paginatedResults.length > 0 ? (
          <>
            {paginatedResults.map((doc) => (
              <div
                key={`${doc.document_id}-${doc.chunk_ind}`}
                className="shrink-0"
              >
                <SearchCard
                  document={doc}
                  isLlmSelected={llmSelectedSet.has(doc.document_id)}
                  onDocumentClick={onDocumentClick}
                />
              </div>
            ))}
          </>
        ) : (
          <IllustrationContent
            illustration={SvgNoResult}
            title="No results found"
            description="Check your connectors/filters or try a different search term."
          />
        )}
      </div>

      {/* ── Bottom row: Pagination ── */}
      {!showEmpty && (
        <Section height="fit">
          <Pagination
            currentPage={currentPage}
            totalPages={totalPages}
            onChange={setCurrentPage}
          />
        </Section>
      )}
    </div>
  );
}
