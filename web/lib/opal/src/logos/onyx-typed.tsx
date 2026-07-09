import type { IconProps } from "@opal/types";
import { cn } from "@opal/utils";

// Matches `web/public/logo.svg` viewBox aspect ratio (935.52 × 482.32).
const LOGO_ASPECT_RATIO = 935.52 / 482.32;

const SvgOnyxTyped = ({ size, className, title, style }: IconProps) => (
	// biome-ignore lint/performance/noImgElement: multicolor SuperPlay mark is a static public SVG asset
	<img
		src="/logo.svg"
		alt={title ?? "SuperPlay"}
		height={size}
		width={size != null ? size * LOGO_ASPECT_RATIO : undefined}
		className={cn("block h-auto w-auto shrink-0", className)}
		style={style}
	/>
);
export default SvgOnyxTyped;
