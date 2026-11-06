import { describe, expect, it } from "vitest";

import { errorMessage, fieldErrors } from "@/features/auth/errors";
import { ApiError } from "@/lib/http";

describe("errorMessage", () => {
  it("maps known codes to student-facing text", () => {
    expect(errorMessage(new ApiError(401, "invalid_credentials", "x", null))).toBe(
      "That email and password don't match.",
    );
  });

  it.each(["constructor", "toString", "__proto__", "hasOwnProperty"])(
    "never returns an inherited Object property for code %s",
    (code) => {
      const message = errorMessage(new ApiError(400, code, "A safe server message.", null));
      expect(typeof message).toBe("string");
      expect(message).toBe("A safe server message.");
    },
  );

  it("uses a generic message for server errors and non-API failures", () => {
    expect(errorMessage(new ApiError(500, "internal_error", "boom", null))).toBe(
      "Something went wrong. Please try again.",
    );
    expect(errorMessage(new TypeError("x"))).toBe("Something went wrong. Please try again.");
  });
});

describe("fieldErrors", () => {
  it("keys errors by field name and keeps the first message per field", () => {
    const error = new ApiError(422, "validation_error", "x", null, {
      fields: [
        { location: "body.email", message: "first" },
        { location: "body.email", message: "second" },
        { location: "body.password", message: "too short" },
      ],
    });

    expect(fieldErrors(error)).toEqual(
      new Map([
        ["email", "first"],
        ["password", "too short"],
      ]),
    );
  });

  it("is not fooled by field names that exist on Object.prototype", () => {
    const error = new ApiError(422, "validation_error", "x", null, {
      fields: [{ location: "body.constructor", message: "odd but valid" }],
    });

    expect(fieldErrors(error)).toEqual(new Map([["constructor", "odd but valid"]]));
  });
});
