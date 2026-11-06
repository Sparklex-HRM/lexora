/**
 * The ONLY server paths the pre-login code (sign-in page, two-step sign-in, forced
 * password change, CAPTCHA challenge, Privacy Officer footer, SSO button, sign-up switch) may name.
 *
 * WASA "API Endpoint Disclosure in JavaScript Files" (fix 2026-10-24, port of the HMS
 * fix): the code served on /login must not carry the application API map
 * (api/endpoints.ts and the other src/api/* modules). App.tsx lazy-loads the signed-in
 * shell; the modules /login still loads statically take their calls from here.
 * Do NOT import another src/api/* module here (type-only imports are fine — erased).
 *
 * Paths are relative to the axios client (baseURL "/api").
 *
 * Honest scope: these requests (and every request after sign-in) remain visible in the
 * browser's Network tab. The control is server-side authorisation (gateway + service
 * role checks); this file only keeps the static map out of the public bundle.
 */
import api from "./client";
import type { User } from "../store/authStore";
import type { TokenResponse } from "./endpoints";
import type { MfaSetup, SignInResult, SignupConfig } from "./saas";

export const PUBLIC_AUTH = {
  login: "/auth/login",
  me: "/auth/me",
  passwordPolicy: "/auth/password-policy",
  changePassword: "/auth/change-password",
  powChallenge: "/auth/challenge",
  loginInfo: "/auth/login-info",
  signupConfig: "/auth/signup/config",
  mfaVerify: "/auth/mfa/challenge/verify",
  mfaEnrollStart: "/auth/mfa/challenge/enroll/start",
  mfaEnrollFinish: "/auth/mfa/challenge/enroll/finish",
  ssoPublic: "/auth/sso/public/",
  ssoStart: "/auth/sso/start/",
} as const;

export const publicAuthApi = {
  login: (email: string, password: string, org_code?: string, captcha_token?: string) =>
    api.post<TokenResponse>(PUBLIC_AUTH.login, { email, password, org_code, captcha_token }).then((r) => r.data),
  me: () => api.get<User>(PUBLIC_AUTH.me).then((r) => r.data),
  signupConfig: () => api.get<SignupConfig>(PUBLIC_AUTH.signupConfig).then((r) => r.data),
  loginInfo: (org_code?: string) =>
    api.get<any>(PUBLIC_AUTH.loginInfo, { params: org_code ? { org_code } : {} }).then((r) => r.data),
  mfaVerify: (mfa_token: string, code: string, captcha_token?: string) =>
    api.post<SignInResult>(PUBLIC_AUTH.mfaVerify, { mfa_token, code, captcha_token }).then((r) => r.data),
  mfaEnrollStart: (mfa_token: string) =>
    api.post<MfaSetup>(PUBLIC_AUTH.mfaEnrollStart, { mfa_token }).then((r) => r.data),
  mfaEnrollFinish: (mfa_token: string, code: string, captcha_token?: string) =>
    api.post<SignInResult>(PUBLIC_AUTH.mfaEnrollFinish, { mfa_token, code, captcha_token }).then((r) => r.data),
  ssoPublicInfo: (orgCode: string) =>
    api.get<{ sso: boolean; required: boolean; display_name: string; org_name: string | null }>(PUBLIC_AUTH.ssoPublic + orgCode).then((r) => r.data),
  ssoStartUrl: (orgCode: string) => `${import.meta.env.VITE_API_BASE || "/api"}${PUBLIC_AUTH.ssoStart}${encodeURIComponent(orgCode)}`,
};
