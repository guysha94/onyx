import type { IconProps } from "@opal/types";
import { cn } from "@opal/utils";

const SvgOnyxLogo = ({ size, className, title, style }: IconProps) => (
	// biome-ignore lint/performance/noImgElement: multicolor SuperPlay mark is a static public SVG asset
	<img
		src="/logo.svg"
		alt={title ?? "SuperPlay"}
		width={size}
		height={size}
		className={cn("block shrink-0 object-contain", className)}
		style={style}
	/>
);
export default SvgOnyxLogo;
