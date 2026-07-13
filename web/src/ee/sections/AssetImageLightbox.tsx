// FORK: miro — image lightbox for search result cards.
// Clicking a Miro asset thumbnail opens this modal with zoom support.
"use client";

import { useEffect, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { cn } from "@opal/utils";
import { Button } from "@opal/components";
import { SvgX, SvgZoomIn, SvgZoomOut } from "@opal/icons";
import { buildImgUrl } from "@/app/app/components/files/images/utils";
import Text from "@/refresh-components/texts/Text";

const ZOOM_STEP = 0.25;
const ZOOM_MIN = 1;
const ZOOM_MAX = 5;

interface AssetImageLightboxProps {
  fileId: string;
  title: string;
  link?: string | null;
  onTitleClick?: () => void;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function AssetImageLightbox({
  fileId,
  title,
  link,
  onTitleClick,
  open,
  onOpenChange,
}: AssetImageLightboxProps) {
  const [scale, setScale] = useState(1);
  const imageRef = useRef<HTMLDivElement>(null);

  // Reset zoom when modal opens.
  useEffect(() => {
    if (open) setScale(1);
  }, [open]);

  // Pre-fetch image so it displays instantly.
  useEffect(() => {
    const img = new Image();
    img.src = buildImgUrl(fileId);
  }, [fileId]);

  function clampScale(next: number) {
    return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, next));
  }

  function handleWheel(e: React.WheelEvent) {
    e.preventDefault();
    e.stopPropagation();
    setScale((prev) => clampScale(prev + (e.deltaY < 0 ? ZOOM_STEP : -ZOOM_STEP)));
  }

  function handleTitleClick(e: React.MouseEvent) {
    e.stopPropagation();
    if (link) {
      window.open(link, "_blank", "noopener,noreferrer");
    } else if (onTitleClick) {
      onTitleClick();
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black bg-opacity-80 z-50 backdrop-blur-xl" />
        <Dialog.Content
          className={cn(
            "fixed z-[100] flex flex-col",
            "top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2",
            "w-full max-w-3xl max-h-[90vh]",
            "bg-background-neutral-01 rounded-lg border border-border-02 shadow-xl",
            "focus:outline-none overflow-hidden"
          )}
        >
          {/* Header: title link + close button */}
          <div className="flex items-center justify-between gap-2 px-4 py-3 border-b border-border-02 shrink-0">
            <button
              type="button"
              onClick={handleTitleClick}
              className={cn(
                "truncate text-left",
                "hover:underline focus:underline focus:outline-none",
                link || onTitleClick ? "cursor-pointer" : "cursor-default"
              )}
            >
              <Text mainUiAction text01 className="truncate">
                {title}
              </Text>
            </button>

            <div className="flex items-center gap-1 shrink-0">
              <Button
                icon={SvgZoomOut}
                size="xs"
                tooltip="Zoom out"
                disabled={scale <= ZOOM_MIN}
                onClick={() => setScale((prev) => clampScale(prev - ZOOM_STEP))}
              />
              <Text secondaryBody text02 className="w-10 text-center tabular-nums">
                {Math.round(scale * 100)}%
              </Text>
              <Button
                icon={SvgZoomIn}
                size="xs"
                tooltip="Zoom in"
                disabled={scale >= ZOOM_MAX}
                onClick={() => setScale((prev) => clampScale(prev + ZOOM_STEP))}
              />
              <Dialog.Close asChild>
                <Button
                  icon={SvgX}
                  size="xs"
                  tooltip="Close"
                  onClick={() => onOpenChange(false)}
                />
              </Dialog.Close>
            </div>
          </div>

          {/* Image area */}
          <div
            ref={imageRef}
            onWheel={handleWheel}
            className="flex-1 overflow-auto flex items-center justify-center p-4 min-h-0"
          >
            <img
              src={buildImgUrl(fileId)}
              alt={title}
              draggable={false}
              style={{
                transform: `scale(${scale})`,
                transformOrigin: "center center",
                transition: "transform 0.15s ease",
              }}
              className="max-w-full max-h-full object-contain select-none"
            />
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
