// @vitest-environment node
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { cspProblems, inlineScripts, readPolicy } from "./check-csp.mjs";

const require = createRequire(import.meta.url);
const webpackConfig = require("../webpack.config.cjs");
const { connectSources, injectContentSecurityPolicy } = webpackConfig;

const THEME = '!function(){document.documentElement.classList.toggle("dark",!0)}();';

function page(themeScript = THEME, head = "") {
  return (
    "<!doctype html><html lang=en><head><meta charset=utf-8>" +
    `<title>NU Connect Hub</title>${head}<script id=theme-init>${themeScript}</script>` +
    '<script defer src=/assets/main.js></script></head><body><div id=root></div></body></html>'
  );
}

const sha256 = (text) => `'sha256-${createHash("sha256").update(text, "utf8").digest("base64")}'`;

function directives(policy) {
  return Object.fromEntries(
    policy.split(";").map((part) => {
      const [name, ...values] = part.trim().split(/\s+/);
      return [name, values];
    }),
  );
}

describe("injectContentSecurityPolicy (the production build hook)", () => {
  it("allows the theme script by the hash of its exact built text", () => {
    const html = injectContentSecurityPolicy(page(), []);
    const policy = directives(readPolicy(html));

    expect(policy["script-src"]).toEqual(["'self'", sha256(THEME)]);
    expect(cspProblems(html)).toEqual([]);
  });

  it("changes the hash when the theme script changes", () => {
    const before = readPolicy(injectContentSecurityPolicy(page(), []));
    const edited = injectContentSecurityPolicy(page(THEME.replace("!0", "!1")), []);

    expect(readPolicy(edited)).not.toEqual(before);
    expect(cspProblems(edited)).toEqual([]);
  });

  it("puts the policy right after the charset, before any script", () => {
    const html = injectContentSecurityPolicy(page(), []);

    expect(html).toMatch(/^<!doctype html><html lang=en><head><meta charset=utf-8><meta http-equiv="Content-Security-Policy"/);
  });

  it("writes the agreed policy, never with unsafe script sources", () => {
    const policy = directives(readPolicy(injectContentSecurityPolicy(page(), [])));

    expect(policy).toEqual({
      "default-src": ["'self'"],
      "script-src": ["'self'", sha256(THEME)],
      "style-src": ["'self'", "'unsafe-inline'"],
      "img-src": ["'self'", "data:", "blob:"],
      "font-src": ["'self'"],
      "media-src": ["'self'", "blob:"],
      "connect-src": ["'self'"],
      "object-src": ["'none'"],
      "base-uri": ["'self'"],
      "form-action": ["'self'"],
    });
  });

  it("adds the API and Socket.IO hosts to connect-src for a split deployment", () => {
    const sources = connectSources({ API_BASE_URL: "https://api.hub.example/api/v1", SOCKET_URL: "https://rt.hub.example" });
    const policy = directives(readPolicy(injectContentSecurityPolicy(page(), sources)));

    expect(policy["connect-src"]).toEqual([
      "'self'",
      "https://api.hub.example",
      "wss://api.hub.example",
      "https://rt.hub.example",
      "wss://rt.hub.example",
    ]);
  });

  it("fails the build when the page has no theme script to hash", () => {
    const html = page().replace(/<script id=theme-init>.*?<\/script>/, "");

    expect(() => injectContentSecurityPolicy(html, [])).toThrow(/theme-init/);
  });
});

describe("connectSources", () => {
  it("adds nothing for same-origin values: unset, empty or a path", () => {
    expect(connectSources({ API_BASE_URL: "/api/v1", SOCKET_URL: "" })).toEqual([]);
    expect(connectSources({ API_BASE_URL: undefined, SOCKET_URL: undefined })).toEqual([]);
  });

  it("gives each absolute origin once, with its WebSocket twin", () => {
    expect(connectSources({ API_BASE_URL: "https://api.hub.example/api/v1", SOCKET_URL: "https://api.hub.example" })).toEqual([
      "https://api.hub.example",
      "wss://api.hub.example",
    ]);
  });

  // socket.io-client takes all of these as SOCKET_URL.
  it.each([
    ["wss://rt.hub.example", ["https://rt.hub.example", "wss://rt.hub.example"]],
    ["wss://rt.hub.example:8443/socket.io", ["https://rt.hub.example:8443", "wss://rt.hub.example:8443"]],
    ["//rt.hub.example", ["https://rt.hub.example", "wss://rt.hub.example"]],
    ["ws://localhost:8000", ["http://localhost:8000", "ws://localhost:8000"]],
    ["http://localhost:8000", ["http://localhost:8000", "ws://localhost:8000"]],
  ])("maps SOCKET_URL %s to both its HTTP and WebSocket forms", (url, expected) => {
    expect(connectSources({ API_BASE_URL: "/api/v1", SOCKET_URL: url })).toEqual(expected);
  });

  it.each(["api.hub.example", "api/v1", "ftp://files.hub.example", "javascript:alert(1)", "https://", "https:///x", "//"])(
    "fails loudly for %s, which is neither a path nor an absolute URL",
    (url) => {
      expect(() => connectSources({ API_BASE_URL: url, SOCKET_URL: "" })).toThrow(/API_BASE_URL/);
      expect(() => connectSources({ API_BASE_URL: "/api/v1", SOCKET_URL: url })).toThrow(/SOCKET_URL/);
    },
  );
});

describe("the production webpack config", () => {
  const plugins = (mode) => webpackConfig({}, { mode }).plugins.map((p) => p.constructor.name);

  it("adds the policy in production builds only (dev HMR needs eval)", () => {
    expect(plugins("production")).toContain("ContentSecurityPolicyPlugin");
    expect(plugins("development")).not.toContain("ContentSecurityPolicyPlugin");
  });

  it("fails the production build when a configured URL can't be parsed", () => {
    const webRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
    const build = (env) =>
      spawnSync(process.execPath, ["-e", 'require("./webpack.config.cjs")({}, { mode: "production" })'], {
        cwd: webRoot,
        encoding: "utf8",
        env: { ...process.env, API_BASE_URL: "/api/v1", SOCKET_URL: "", ...env },
      });

    expect(build({}).status).toBe(0);
    const broken = build({ SOCKET_URL: "rt.hub.example" });
    expect(broken.status).not.toBe(0);
    expect(broken.stderr).toContain("SOCKET_URL");
  });

  it("emits every font as a file, since font-src allows only 'self' (never a data: URI)", () => {
    const { rules } = webpackConfig({}, { mode: "production" }).module;
    for (const font of ["manrope-cyrillic-ext.woff2", "manrope.woff"]) {
      const rule = rules.find((r) => r.test instanceof RegExp && r.test.test(font));

      expect(rule?.type, font).toBe("asset/resource");
    }
  });
});

describe("inlineScripts", () => {
  it("returns the text of inline scripts only, quoted or not", () => {
    const html = '<script id="a">one()</script><script src="/x.js"></script><script id=b>two()</script>';

    expect(inlineScripts(html)).toEqual([
      { id: "a", text: "one()" },
      { id: "b", text: "two()" },
    ]);
  });
});

describe("cspProblems (the post-build check)", () => {
  const good = () => injectContentSecurityPolicy(page(), []);

  it("passes a page whose policy matches its inline script", () => {
    expect(cspProblems(good())).toEqual([]);
  });

  it("fails a page with no policy", () => {
    expect(cspProblems(page())).toEqual([expect.stringMatching(/no Content-Security-Policy meta tag/)]);
  });

  it("fails a page whose hash no longer matches the theme script", () => {
    const stale = good().replace(THEME, THEME.replace("!0", "!1"));

    expect(cspProblems(stale)).toEqual(
      expect.arrayContaining([
        expect.stringMatching(/inline script #theme-init is not allowed/),
        expect.stringMatching(/hash .* matches no inline script/),
      ]),
    );
  });

  it("fails a page with an extra inline script the policy doesn't cover", () => {
    const extra = good().replace("</head>", "<script>alert(1)</script></head>");

    expect(cspProblems(extra)).toEqual([expect.stringMatching(/inline script number 2 is not allowed/)]);
  });

  it("fails a policy that lets any inline or evaluated script run", () => {
    for (const loose of ["'unsafe-inline'", "'unsafe-eval'", "*", "https:", "data:"]) {
      const html = good().replace("script-src 'self'", `script-src 'self' ${loose}`);

      expect(cspProblems(html)).toEqual([expect.stringContaining(`script-src allows ${loose}`)]);
    }
  });

  it("fails a policy placed after a script, which the browser would not apply to it", () => {
    const html = good();
    const meta = html.match(/<meta http-equiv="Content-Security-Policy"[^>]*>/)[0];
    const moved = html.replace(meta, "").replace("</head>", `${meta}</head>`);

    expect(cspProblems(moved)).toEqual([expect.stringMatching(/must come before every script/)]);
  });

  describe("connect-src against the configured API and Socket.IO URLs", () => {
    const split = { API_BASE_URL: "https://api.hub.example/api/v1", SOCKET_URL: "wss://api.hub.example" };
    const built = (configured) => injectContentSecurityPolicy(page(), connectSources(configured));

    it("passes a page built for the configuration it is checked against", () => {
      expect(cspProblems(built(split), split)).toEqual([]);
      expect(cspProblems(good(), {})).toEqual([]);
    });

    it("fails a stale connect-src, built for another API host", () => {
      const stale = built({ API_BASE_URL: "https://api.old.example/api/v1" });

      expect(cspProblems(stale, split)).toEqual(
        expect.arrayContaining([
          expect.stringMatching(/connect-src lacks https:\/\/api\.hub\.example/),
          expect.stringMatching(/connect-src allows https:\/\/api\.old\.example/),
        ]),
      );
    });

    it("fails a same-origin page once a split API is configured", () => {
      expect(cspProblems(good(), split)).toEqual([
        expect.stringMatching(/lacks https:\/\/api\.hub\.example/),
        expect.stringMatching(/lacks wss:\/\/api\.hub\.example/),
      ]);
    });

    it("fails a loosened connect-src", () => {
      for (const loose of ["https:", "wss:", "*", "https://*.hub.example"]) {
        const html = built(split).replace("connect-src 'self'", `connect-src 'self' ${loose}`);

        expect(cspProblems(html, split)).toEqual([expect.stringContaining(`connect-src allows ${loose}`)]);
      }
    });

    it("fails a policy with no connect-src at all", () => {
      const html = good().replace(/connect-src [^;]*; /, "");

      expect(cspProblems(html, {})).toEqual([expect.stringMatching(/no connect-src directive/)]);
    });

    it("reports a configured value that isn't a URL instead of guessing", () => {
      expect(cspProblems(good(), { SOCKET_URL: "rt.hub.example" })).toEqual([expect.stringMatching(/SOCKET_URL/)]);
    });
  });

  it("fails a page with two policies", () => {
    const html = good();
    const meta = html.match(/<meta http-equiv="Content-Security-Policy"[^>]*>/)[0];

    expect(cspProblems(html.replace(meta, meta + meta))).toEqual([expect.stringMatching(/2 Content-Security-Policy/)]);
  });
});
