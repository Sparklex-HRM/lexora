// Province / territory names for the 2-letter codes the backend stores
// (shared/utils/enums.py PROVINCES_ALL — the 13 Canadian provinces and territories). Display only.
export const PROVINCE_NAMES: Record<string, string> = {
  ON: "Ontario", BC: "British Columbia", AB: "Alberta", QC: "Quebec", MB: "Manitoba", SK: "Saskatchewan",
  NS: "Nova Scotia", NB: "New Brunswick", NL: "Newfoundland and Labrador", PE: "Prince Edward Island",
  NT: "Northwest Territories", NU: "Nunavut", YT: "Yukon",
};

/** Kept keyed by country so callers read `REGION_NAMES.CA[code]`; Lexora HRM is Canada-only. */
export const REGION_NAMES: Record<"CA", Record<string, string>> = { CA: PROVINCE_NAMES };

export const provinceName = (code: string | null | undefined) => (code ? PROVINCE_NAMES[code] || code : "");

export const regionOptions = (_country?: string | null) =>
  Object.entries(PROVINCE_NAMES)
    .map(([value, label]) => ({ value, label }))
    .sort((a, b) => a.label.localeCompare(b.label));

export const COUNTRY_NAME: Record<string, string> = { CA: "Canada" };
