import { APIError, scoreBatchFit, uploadBatch } from "@/lib/api";

// "Try with sample data": import the bundled 10-lead sample (public/
// sample-leads.csv) through the normal upload and ranking endpoints, so the
// result is an ordinary import the rep can work through. Nothing is drafted,
// approved or sent automatically.
export const SAMPLE_CSV_URL = "/sample-leads.csv";

/**
 * Rank a fresh import against the active seller profile's target customers.
 * Returns null when ranked, or a note to show when ranking needs setup first
 * (the import itself succeeded either way; leads stay unranked).
 */
export async function rankImport(batchId: string): Promise<string | null> {
  try {
    await scoreBatchFit(batchId);
    return null;
  } catch (e) {
    if (e instanceof APIError && e.status === 409) {
      return `Imported, but not ranked: ${e.detail ?? e.message}`;
    }
    throw e;
  }
}

export async function loadSampleLeads(): Promise<string> {
  const response = await fetch(SAMPLE_CSV_URL);
  if (!response.ok) throw new Error("The sample file could not be loaded.");
  const file = new File([await response.blob()], "sample-leads.csv", { type: "text/csv" });
  const upload = await uploadBatch(file, "Sample leads");
  await rankImport(upload.batch_id);  // the Leads page explains any missing setup
  return upload.batch_id;
}
