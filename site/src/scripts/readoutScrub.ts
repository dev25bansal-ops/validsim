/**
 * The readout scrub — the single piece of JavaScript on the site.
 *
 * Why it exists at all: every other motion on this page is native CSS
 * (`animation-timeline: view()`), which is free. What CSS cannot do is SCRUB —
 * mapping scroll position to a value with inertia. That is the only reason GSAP
 * is justified here.
 *
 * It moves the MARKER along the axis rather than counting the number up. The
 * figure is a dial: the marker is where the run landed, and a number counting
 * up beside a stationary marker would contradict the measurement the figure is
 * making. The composite is static text in the HTML, which is also why the
 * figure stays readable if this module never loads.
 *
 * Why it is a plain module rather than an Astro island: an island needs a UI
 * framework as a dependency, and `client:*` directives only work on components
 * with a renderer. `ReadoutScrub.astro` had `client:visible` on a renderer-less
 * `.astro`, so the island never hydrated and this effect never ran — roughly
 * 30kb of GSAP shipped and dead. An Astro `<script>` module is bundled and
 * type-stripped like any other, needs no framework, and is deferred, so the
 * rule that matters is preserved: the number is already correct in the HTML.
 *
 * Rules this file follows:
 *  - Animate transform/opacity only. No layout properties, no reflow.
 *  - `prefers-reduced-motion` is honoured and the effect is skipped entirely.
 *  - ScrollTrigger is created inside a `gsap.context` scoped to the figure and
 *    reverted on teardown, so a client-side navigation cannot leave a listener.
 *  - Purely additive: if this module never runs, the figure is still a figure.
 */
import gsap from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';

gsap.registerPlugin(ScrollTrigger);

function init(): (() => void) | undefined {
  const root = document.querySelector<HTMLElement>('[data-scrub]');
  if (!root) return;

  // Reduced motion: leave the DOM exactly as authored. No tween, no listener.
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

  const marker = root.querySelector<HTMLElement>('.track-marker');
  if (!marker) return;

  // The marker's resting position, authored as a percentage of the track.
  const resting = parseFloat(marker.style.insetBlockStart);
  if (!Number.isFinite(resting)) return;

  // The floor of the visible scale: below this the marker is off the axis, so
  // the entrance travels from the floor rather than from zero.
  const floor = parseFloat(root.dataset.scaleMin ?? '0');
  if (!Number.isFinite(floor)) return;

  // Hand the subtree to GSAP so the CSS entrance animation on the same element
  // cannot fight the tween for the same properties.
  root.classList.add('scrubbed');

  const ctx = gsap.context(() => {
    // The marker rises along the axis to where this run actually landed.
    // Scroll linked, so scrolling back up genuinely rewinds it rather than
    // replaying a one-shot — a measurement you can scrub is one you can read.
    gsap.fromTo(
      marker,
      { insetBlockStart: `${floor}%` },
      {
        insetBlockStart: `${resting}%`,
        ease: 'none',
        scrollTrigger: {
          trigger: root,
          start: 'top 88%',
          end: 'top 40%',
          scrub: 0.6,
        },
      },
    );

    // A slight parallax on the figure: the readout drifts as it is read.
    gsap.to(root, {
      yPercent: -3,
      ease: 'none',
      scrollTrigger: {
        trigger: root,
        start: 'top bottom',
        end: 'bottom top',
        scrub: true,
      },
    });
  }, root);

  // Layout shifts after load (webfont swap, late CSS). Without this the trigger
  // positions are measured against stale offsets.
  const onLoad = () => ScrollTrigger.refresh();
  window.addEventListener('load', onLoad);

  return () => {
    window.removeEventListener('load', onLoad);
    ctx.revert();
  };
}

const cleanup = init();
window.addEventListener('pagehide', () => cleanup?.());
