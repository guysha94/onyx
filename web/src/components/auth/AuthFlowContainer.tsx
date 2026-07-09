"use client";

import type { ReactNode } from "react";
import Image from "next/image";
import { Text } from "@opal/components";
import { markdown } from "@opal/utils";
import { useSettings } from "@/lib/settings/hooks";

const CSS_COINS = [
  "auth-css-coin--1",
  "auth-css-coin--2",
  "auth-css-coin--3",
  "auth-css-coin--4",
  "auth-css-coin--5",
  "auth-css-coin--6",
] as const;

interface AuthFlowContainerProps {
  children: ReactNode;
  authState?: "signup" | "login" | "join";
  footerContent?: ReactNode | string;
}

function renderLoginFooter(
  footerContent: ReactNode | string | undefined,
  appName: string,
): ReactNode {
  if (typeof footerContent === "string") {
    return (
      <Text as="p" font="secondary-body">
        {markdown(footerContent)}
      </Text>
    );
  }

  if (footerContent) {
    return footerContent;
  }

  return (
    <Text as="p" font="secondary-body">
      {markdown(`New to ${appName}? [Create an Account](/auth/signup)`)}
    </Text>
  );
}

export default function AuthFlowContainer({
  children,
  authState,
  footerContent,
}: AuthFlowContainerProps) {
  const { appName } = useSettings();

  return (
    <div className="auth-page">
      <div aria-hidden="true">
        {CSS_COINS.map((coinClass) => (
          <div key={coinClass} className={`auth-css-coin ${coinClass}`}>
            <div className="auth-css-coin__face" />
          </div>
        ))}
      </div>

      <div className="auth-page-content">
        <Image
          src="/logo.svg"
          alt="SuperPlay"
          width={200}
          height={103}
          priority
          className="auth-logo"
        />

        <h1 className="auth-headline">It&apos;s only fun if you&apos;re winning</h1>

        <div className="auth-card">
          {children}

          {authState === "login" && (
            <div className="auth-card-footer">
              {renderLoginFooter(footerContent, appName)}
            </div>
          )}

          {authState === "signup" && (
            <div className="auth-card-footer">
              <Text as="p" font="secondary-body">
                {markdown(
                  "Already have an account? [Sign In](/auth/login?autoRedirectToSignup=false)",
                )}
              </Text>
            </div>
          )}
        </div>

        <div className="auth-mascot">
          <Image
            src="/peon_hello.png"
            alt=""
            width={220}
            height={220}
            priority
          />
        </div>
      </div>
    </div>
  );
}
