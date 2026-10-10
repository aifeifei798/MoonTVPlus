/**
 * `next/link` — a plain anchor.
 *
 * Next's `Link` intercepts clicks and performs a soft client-side navigation,
 * and its `onClick` prop is a callback it runs right before that navigation
 * (here: start the loading spinner, remember which nav item is active).
 *
 * The shim renders `<a href>`, so the browser does the navigation itself, and
 * `onClick` passes through as a normal React event listener: it still runs
 * before the navigation starts, which is what the nav-bar spinner and active
 * styling key off. Dropping it (as an earlier version did) left the spinner
 * and the active state dead.
 */
import * as React from 'react';

export default function Link(props: Record<string, unknown>) {
  // `key` never reaches a component (React strips it), so there is nothing
  // to delete; `onClick` passes through as a React event listener, which is
  // what the nav bars rely on for their spinner/active-state updates.
  const { key: _key, ...attrs } = props;

  return <a {...attrs} />;
}
