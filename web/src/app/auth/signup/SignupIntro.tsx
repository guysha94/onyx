"use client";

import { useSettings } from "@/lib/settings/hooks";

interface SignupIntroProps {
  cloud: boolean;
}

export default function SignupIntro({ cloud }: SignupIntroProps) {
  const { appName } = useSettings();

  return (
    <div className="auth-card-intro">
      <h2 className="auth-card-title">
        {cloud ? "Complete your sign up" : "Create account"}
      </h2>
      <p className="auth-card-subtext">{`Get started with ${appName}`}</p>
    </div>
  );
}
