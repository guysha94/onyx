import SvgOnyxTyped from "@opal/logos/onyx-typed";

interface OnyxLogoTypedProps {
	size?: number;
	className?: string;
}

const SvgOnyxLogoTyped = ({ size: height, className }: OnyxLogoTypedProps) => (
	<SvgOnyxTyped size={height} className={className} />
);
export default SvgOnyxLogoTyped;
