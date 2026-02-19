"use client"

import { ReactNode, PropsWithChildren } from "react"
import { useAuth } from "react-oidc-context"

function AutoSigninContent({ children }: PropsWithChildren) {
  const auth = useAuth()

  if (auth.isLoading) {
    return <div className="flex items-center justify-center min-h-screen text-xl">Loading...</div>
  }

  if (!auth.isAuthenticated) {
    return (
      <div className="flex flex-col items-center justify-center min-h-screen gap-4">
        <p className="text-4xl">Please sign in</p>
        <button
          className="rounded-md border border-brand-dark/20 bg-brand-dark px-4 py-2 text-sm font-medium text-white transition hover:bg-brand-dark/90"
          onClick={() => auth.signinRedirect()}
          type="button"
        >
          Sign In
        </button>
      </div>
    )
  }

  return <>{children}</>
}

export function AutoSignin({ children }: { children: ReactNode }) {
  if (typeof window === "undefined") {
    return null
  }

  return <AutoSigninContent>{children}</AutoSigninContent>
}
