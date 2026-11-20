/**
 * Which VOOL on a computer this app works with. Each side carries one small number for the language it
 * speaks; a side from before this check sends nothing and counts as 1. The numbers only choose plain
 * "update" advice: they never grant anything, and the computer still checks every request itself.
 */
export const APP_API = 2;
/** The oldest computer this app still works with. */
export const MIN_COMPUTER_API = 1;

export type Compat = {companion_api?: unknown; min_app_api?: unknown; newest_app_api?: unknown};
export type Fit = "ok" | "update_app" | "update_computer";

export function apiLevel(value: unknown): number {
  const number = typeof value === "string" && /^\d{1,5}$/.test(value) ? Number(value) : value;
  return typeof number === "number" && Number.isInteger(number) && number >= 1 && number <= 10_000 ? number : 1;
}

export function fit(compat: Compat | null | undefined): Fit {
  if (apiLevel(compat?.min_app_api) > APP_API) return "update_app";
  if (apiLevel(compat?.companion_api) < MIN_COMPUTER_API) return "update_computer";
  return "ok";
}

/** The pairing code carries the computer's numbers as api= and minapp=; a code without them is from before the check. */
export function fitFromCode(params: Map<string, string>): Fit {
  return fit({companion_api: params.get("api") || undefined, min_app_api: params.get("minapp") || undefined});
}

/** A newer app exists than this one, as far as this computer knows. */
export function newerAppKnown(compat: Compat | null | undefined): boolean {
  return apiLevel(compat?.newest_app_api) > APP_API;
}
