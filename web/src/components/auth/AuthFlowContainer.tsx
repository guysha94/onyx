"use client";

import Image from "next/image";
import Logo from "@/refresh-components/Logo";
import { Text } from "@opal/components";
import { markdown } from "@opal/utils";
import { useSettings } from "@/lib/settings/hooks";

interface AuthFlowContainerProps {
  children: React.ReactNode;
  authState?: "signup" | "login" | "join";
  footerContent?: React.ReactNode;
}

export default function AuthFlowContainer({
  children,
  authState,
  footerContent,
}: AuthFlowContainerProps) {
  const { appName } = useSettings();

  return (
    <div className="flex min-h-screen w-full bg-background">
      <div className="flex w-full flex-col items-center justify-center px-6 py-16 sm:px-10 lg:w-1/2 lg:px-20">
        <div className="flex w-full max-w-md flex-col items-start">
          <Logo folded size={40} className="text-theme-primary-05" />
          <div className="mt-8 w-full">{children}</div>

          {authState === "login" && (
            <div className="mt-8 w-full text-center">
              {footerContent ? (
                <p className="font-main-ui-body text-text-03">
                  {footerContent}
                </p>
              ) : (
                <Text as="p" font="main-ui-body" color="text-03">
                  {markdown(
                    `New to ${appName}? [Create an Account](/auth/signup)`
                  )}
                </Text>
              )}
            </div>
          )}

          {authState === "signup" && (
            <div className="mt-8 w-full text-center">
              <Text as="p" font="main-ui-body" color="text-03">
                {markdown(
                  "Already have an account? [Sign In](/auth/login?autoRedirectToSignup=false)"
                )}
              </Text>
            </div>
          )}
        </div>
      </div>

      {/* Illustrated warm panel — hidden on mobile/tablet per the responsive
          design pass; only the form matters below the `lg` breakpoint. */}
      <div
        aria-hidden="true"
        className="relative hidden w-1/2 shrink-0 items-center justify-center border-l border-border-01 bg-background-tint-02 lg:flex"
      >
        <Image
          src="/peon_hello.png"
          alt=""
          width={520}
          height={520}
          priority
          className="h-auto w-full max-w-sm object-contain px-12"
        />
      </div>
    </div>
  );
}
