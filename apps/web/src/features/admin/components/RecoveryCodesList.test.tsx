import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RecoveryCodesList } from "@/features/admin/components/RecoveryCodesList";

describe("RecoveryCodesList", () => {
  it("renders every code given, as-is", () => {
    const codes = ["ABCD-EFGH-1111", "ABCD-EFGH-2222", "ABCD-EFGH-3333"];
    render(<RecoveryCodesList codes={codes} />);

    for (const code of codes) expect(screen.getByText(code)).toBeInTheDocument();
  });
});
