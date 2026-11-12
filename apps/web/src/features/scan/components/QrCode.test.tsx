import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { QrCode } from "@/features/scan/components/QrCode";

// A tiny hand-built matrix: true = dark module. Real matrices come from uqr's encode().
const MATRIX = {
  size: 3,
  data: [
    [true, false, true],
    [false, true, false],
    [true, false, true],
  ],
};

describe("QrCode", () => {
  it("honours the matrix size with a 4-module quiet zone on every side", () => {
    const { container } = render(<QrCode matrix={MATRIX} aria-label="QR code for your profile link" />);

    expect(container.querySelector("svg")).toHaveAttribute("viewBox", "0 0 11 11");
  });

  it("renders one rect per dark module, inset by the quiet zone", () => {
    const { container } = render(<QrCode matrix={MATRIX} aria-label="QR code for your profile link" />);

    const modules = container.querySelectorAll('[data-slot="qr-module"]');
    expect(modules).toHaveLength(5);
    expect(modules[0]).toHaveAttribute("x", "4");
    expect(modules[0]).toHaveAttribute("y", "4");
  });

  it("exposes the accessible name passed in", () => {
    render(<QrCode matrix={MATRIX} aria-label="QR code for your profile link" />);

    expect(screen.getByRole("img", { name: "QR code for your profile link" })).toBeInTheDocument();
  });
});
