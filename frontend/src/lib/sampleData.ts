import { scoreBatch, uploadBatch } from "@/lib/api";

// "Try with sample data": import the bundled 10-lead sample (public/
// sample-leads.csv) through the normal upload and scoring endpoints, so the
// result is an ordinary import the rep can work through. Nothing is drafted,
// approved or sent automatically.
export const SAMPLE_CSV_URL = "/sample-leads.csv";

export async function loadSampleLeads(): Promise<string> {
  const response = await fetch(SAMPLE_CSV_URL);
  if (!response.ok) throw new Error("The sample file could not be loaded.");
  const file = new File([await response.blob()], "sample-leads.csv", { type: "text/csv" });
  const upload = await uploadBatch(file, "Sample leads");
  await scoreBatch(upload.batch_id);
  return upload.batch_id;
}
