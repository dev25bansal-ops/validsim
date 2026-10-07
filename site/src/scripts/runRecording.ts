/**
 * Playback control for the run recording.
 *
 * Autoplay is not a control. A reader who has been watching a silent looping
 * clip has learned nothing about how to start it, so the video also exposes a
 * real button that pauses and resumes. It sits over the frame and is present
 * whenever playback is NOT running, which means the browser's own control bar
 * (the video has `controls`) is what a reader reaches for mid-playback — two
 * controls would only disagree.
 *
 * Nothing here is needed to watch the video. The markup carries `controls`, a
 * `poster` and `preload="none"`; if this module never loads, the reader gets
 * the browser's control bar on a poster frame.
 */
const root = document.querySelector<HTMLElement>('[data-runrec]');
const video = root?.querySelector<HTMLVideoElement>('video');
const toggle = root?.querySelector<HTMLButtonElement>('[data-play-toggle]');

if (root && video && toggle) {
  const NEAR_END = 0.25;

  const show = () => {
    root.dataset.playing = '0';
    toggle.textContent = 'Play the run';
  };
  const hide = () => {
    root.dataset.playing = '1';
    toggle.textContent = 'Replay the run';
  };

  toggle.addEventListener('click', () => {
    if (video.paused) {
      // A looped clip sits at its last frame between passes, so "play" from
      // there would replay only the tail.
      if (video.duration - video.currentTime < NEAR_END) video.currentTime = 0;
      void video.play();
    } else {
      video.pause();
    }
  });

  // The state is driven by the element, not by the button, so scrubbing with
  // the native controls and pressing the space bar both keep the overlay honest.
  video.addEventListener('playing', hide);
  video.addEventListener('pause', show);
  video.addEventListener('ended', show);
  video.addEventListener('waiting', hide);

  // Autoplay refused — battery saver, low-power mode, a browser policy. Say so
  // rather than leaving a control that appears to do nothing.
  video.addEventListener('error', () => {
    show();
    toggle.textContent = 'Video unavailable';
    toggle.disabled = true;
  });
}