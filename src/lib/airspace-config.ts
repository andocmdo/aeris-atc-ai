export const AIRSPACE_DISABLED_HEADER = "x-aeris-airspace-disabled";
export const AIRSPACE_DISABLED_REASON = "missing-openaip-api-key";

export function getOpenAipApiKey(): string | null {
  const apiKey = process.env.OPENAIP_API_KEY?.trim();
  return apiKey ? apiKey : null;
}

export function isAirspaceConfigured(): boolean {
  return getOpenAipApiKey() !== null;
}
