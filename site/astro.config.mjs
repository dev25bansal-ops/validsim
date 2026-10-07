// @ts-check
import { defineConfig } from 'astro/config';

/**
 * ValidSim marketing site.
 *
 * Two deliberate constraints, both recorded in ../../.impeccable.md:
 *
 * 1. ZERO RUNTIME JS BY DEFAULT. Astro ships no JavaScript unless a component
 *    opts in with a `client:*` directive. Exactly one component does (the
 *    scroll-scrubbed readout), and it is marked `client:visible` so it is not
 *    even downloaded until it scrolls into view. A reviewer can verify this by
 *    viewing source: one <script type="module">, deferred, and nothing else.
 *
 * 2. NO MIDDLEWARE, NO SSR. `output: 'static'` means the build emits HTML and
 *    CSS only. There is no server to run, no runtime to patch, and no cold
 *    start. The whole site is deployable to any static host.
 */
export default defineConfig({
  site: 'https://validsim.com',
  output: 'static',
  trailingSlash: 'ignore',
  build: {
    // Fail the build on a broken link or a malformed component rather than
    // shipping it. A marketing page that 404s its own nav is worse than no site.
    format: 'directory',
  },
  devToolbar: { enabled: false },
  compressHTML: true,
});
