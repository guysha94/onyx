"use client";

import { AuthTypeMetadata } from "@/lib/auth/types";
import LoginText from "@/app/auth/login/LoginText";
import SignInButton from "@/app/auth/login/SignInButton";
import EmailPasswordForm from "./EmailPasswordForm";
import { AuthType, NEXT_PUBLIC_FORGOT_PASSWORD_ENABLED } from "@/lib/constants";
import { useSendAuthRequiredMessage } from "@/lib/extension/utils";
import { Button, MessageCard, Text } from "@opal/components";
import { markdown } from "@opal/utils";

interface LoginPageProps {
  authUrl: string | null;
  authTypeMetadata: AuthTypeMetadata | null;
  nextUrl: string | null;
  hidePageRedirect?: boolean;
  verified?: boolean;
  isFirstUser?: boolean;
}

export default function LoginPage({
  authUrl,
  authTypeMetadata,
  nextUrl,
  hidePageRedirect,
  verified,
  isFirstUser,
}: LoginPageProps) {
  useSendAuthRequiredMessage();

  // Honor any existing nextUrl; only default to new team flow for first users with no nextUrl
  const effectiveNextUrl =
    nextUrl ?? (isFirstUser ? "/app?new_team=true" : null);

  return (
    <div className="flex flex-col w-full justify-center">
      {verified && (
        <MessageCard
          variant="success"
          title="Your email has been verified! Please sign in to continue."
        />
      )}
      {authUrl &&
        authTypeMetadata &&
        authTypeMetadata.authType !== AuthType.CLOUD &&
        // basic auth is handled below w/ the EmailPasswordForm
        authTypeMetadata.authType !== AuthType.BASIC && (
          <div className="flex flex-col w-full gap-6">
            <LoginText />
            <SignInButton
              authorizeUrl={authUrl}
              authType={authTypeMetadata?.authType}
            />
          </div>
        )}

      {authTypeMetadata?.authType === AuthType.CLOUD && (
        <div className="w-full justify-center flex flex-col gap-6">
          <LoginText />
          {authUrl && authTypeMetadata && (
            <>
              <SignInButton
                authorizeUrl={authUrl}
                authType={authTypeMetadata?.authType}
              />
              <div className="flex w-full flex-row items-center gap-2">
                <div className="flex-1 border-t border-border-02" />
                <Text as="p" font="main-ui-muted" color="text-03">
                  or
                </Text>
                <div className="flex-1 border-t border-border-02" />
              </div>
            </>
          )}
          <EmailPasswordForm shouldVerify={true} nextUrl={effectiveNextUrl} />
          {NEXT_PUBLIC_FORGOT_PASSWORD_ENABLED && (
            <Button href="/auth/forgot-password">Reset Password</Button>
          )}
        </div>
      )}

      {authTypeMetadata?.authType === AuthType.BASIC && (
        <div className="flex flex-col w-full gap-6">
          <LoginText />
          <EmailPasswordForm nextUrl={effectiveNextUrl} />
        </div>
      )}

      {!hidePageRedirect && (
        <div className="mt-6 text-center">
          <Text as="p" font="main-ui-body" color="text-03">
            {markdown(
              "Don't have an account? [Create an account](/auth/signup)"
            )}
          </Text>
        </div>
      )}
    </div>
  );
}
