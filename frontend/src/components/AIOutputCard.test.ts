import { describe, expect, it } from "vitest";

import { readableId } from "./AIOutputCard";

describe("readableId", () => {
  it("turns cited ids into words", () => {
    expect(readableId("fact-company_size")).toBe("company size");
    expect(readableId("fact-extra-pain_points")).toBe("pain points");
    expect(readableId("cap-2")).toBe("capability 2");
    expect(readableId("claim-1")).toBe("claim 1");
    expect(readableId("current_tools_or_product_usage")).toBe("current tools or product usage");
  });
});
