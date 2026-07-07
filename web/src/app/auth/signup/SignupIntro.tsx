"use client";

import { useSettings } from "@/lib/settings/hooks";
import Text from "@/refresh-components/texts/Text";

interface SignupIntroProps {
  cloud: boolean;
}

export default function SignupIntro({ cloud }: SignupIntroProps) {
  const { appName } = useSettings();

  return (
    <div className="w-full">
      <Text as="p" headingH2 text05>
        {cloud ? "Complete your sign up" : "Create account"}
      </Text>
      <Text as="p" text03>
        Get started with {appName}
      </Text>
    </div>
  );
}
