# Tablekeeper — design direction (stage 2)

Character: a warm, confident neighbourhood restaurant. Cream paper, ink text, wine-red as the one primary
colour, and a brass hairline accent. Headings are set in a bookish serif and the interface in a quiet
sans-serif. Restaurants and tables are named the way a host would say them ("Table 2", "Tables 1 + 2"), never
as raw ids.

Every asset ships in the binary. There are no web fonts: the type stacks below use fonts already installed on
the visitor's system, so nothing is fetched at run time.

## Type
| Token | Size / line height | Use |
|---|---|---|
| `--fs-xs` | 13 / 18 px | captions, ids where helpful, legend |
| `--fs-sm` | 14 / 20 px | secondary text, field hints |
| `--fs-md` | 16 / 24 px | body, inputs, buttons (never below 16 px on inputs: no iOS zoom) |
| `--fs-lg` | 20 / 28 px | card titles, time headings in the grid |
| `--fs-xl` | 25 / 32 px | screen titles on mobile |
| `--fs-2xl` | 31 / 38 px | screen titles ≥ 768 px; confirmation reference |

- Display (headings, reference): `"Iowan Old Style", "Palatino Linotype", Palatino, "Book Antiqua", Georgia, serif`, weight 600.
- Interface: `system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif`, weights 400/600.
- Reference code: `ui-monospace, "Cascadia Mono", Consolas, monospace`, letter-spacing 0.08em.

## Colour (all text pairs meet WCAG AA ≥ 4.5:1; large or graphic elements ≥ 3:1)
| Token | Hex | Role |
|---|---|---|
| `--paper` | #FBF6EE | page background |
| `--surface` | #FFFFFF | cards, inputs |
| `--ink` | #2B1D16 | body text (15.1:1 on paper) |
| `--muted` | #6B5A4E | secondary text (6.1:1 on paper; 6.6:1 on white) |
| `--line` | #E4D9CB | hairlines, card borders |
| `--wine` | #8A2432 | primary actions, selected state (8.8:1 for white text; 8.2:1 as text on paper) |
| `--wine-dark` | #6E1A27 | primary hover/pressed |
| `--brass` | #B07A2A | decorative rule under the brand, focus-ring halo |
| `--focus` | #1F5FBF | focus ring, 3 px outline + 2 px offset; the offset gap is paper, so the ring always sits on paper (5.7:1) |
| `--ok-ink` / `--ok-bg` | #1E5B34 / #E7F2EA | available cells, success (7.0:1) |
| `--no-ink` / `--no-bg` | #75685E / #F1ECE6 | unavailable cells (4.6:1; the hatch is confined to a 6 px edge strip so it never sits behind text) |
| `--err-ink` / `--err-bg` | #9E2418 / #FCECE9 | refused, error, conflict (6.7:1) |
| `--warn-ink` / `--warn-bg` | #6E4500 / #FFF3D6 | uncertain, stale (7.6:1) |

Colour is never the only signal. Every state also has a text label, an icon glyph or a pattern.

## Spacing, radius, elevation, motion
- Spacing scale (4 px base): 4, 8, 12, 16, 24, 32, 48, 64 (`--s1` … `--s8`). Page gutter 16 px at 375 px,
  24 px at ≥ 768 px; content max width 1120 px.
- Radius: 6 px for controls and cells, 12 px for cards and panels, 999 px for pills.
- Elevation: one shadow for floating panels (`0 1px 2px rgba(43,29,22,.06), 0 8px 24px rgba(43,29,22,.08)`).
- Motion: 120 ms ease-out for hover/press/focus, 200 ms for panel entrance (fade + 4 px rise), 1.2 s shimmer
  for the loading skeleton. `prefers-reduced-motion: reduce` turns every animation off.
- Hit targets are at least 44 × 44 px on touch widths.

## Layout
- A header on every route shows the brand "Tablekeeper" and the navigation (Find a table · Look up a booking).
  Signed in, it adds the diner's name (`current-user`) and "Sign out". Signed out, it shows "Sign in" and
  "Create account".
- `/` is a search card (restaurant, date, party size, Search) above the results. At ≥ 1024 px the booking panel
  sits beside the results in a sticky column. Below 1024 px it follows the results, and the page scrolls to it.
- The availability grid is a list of time rows. Each row has its time (serif, `--fs-lg`) and a wrapping set of
  table cells: single tables first, then a "Combined" group. Cells wrap instead of scrolling, so 375 px has no
  horizontal scroll.
- `/signup`, `/login` and `/lookup` are single centred cards, 440 px max.

## States
| State | Look |
|---|---|
| Available cell | Sage tint `--ok-bg`, `--ok-ink` text "Table 2", seats below ("4 seats"), 1 px `--ok-ink` border. Hover darkens the border; the cursor is a pointer. |
| Unavailable cell | `--no-bg` with a 45° hatch strip on the leading edge, `--no-ink` text, no pointer; its accessible name ends "not available". It stays an enabled button (no `disabled`/`aria-disabled`) so a click is accepted and does nothing. |
| Selected cell | Filled `--wine` with white text, a check glyph and `aria-pressed="true"`. |
| Combination cell | Same states as a single cell. The label is "Tables 1 + 2" with a link glyph and the summed seats. It sits in a "Combined tables" group so it reads as an intentional option. |
| Loading | The Search button shows "Searching…" and is busy. The results area shows a skeleton of three time rows with shimmering pills, plus `aria-busy="true"`. During submit the booking button shows "Booking…". |
| Empty (closed day / no slots) | A card with a moon glyph: "No tables on this day", plus the hint "The restaurant is closed or fully booked — try another date." (`no-slots`). |
| Before first search | A soft prompt card: "Choose a restaurant, date and party size to see open tables." |
| Success | A confirmation card with a green rule and check glyph: "You're booked". The reference is large in the monospace stack. Details list the restaurant, time and tables. The form stays above it. |
| Refused (4xx) | `booking-error` / `auth-error` / `reservation-error`: `--err-bg` panel with an "!" glyph and a plain-language message. `role="alert"`. |
| Conflict (409 `table_unavailable`) | The refused panel says "Someone just took that table. Availability has been refreshed — pick another time or table." The form stays open with its inputs, and the grid re-renders with fresh data. |
| Uncertain (no response) | `booking-uncertain`: `--warn-bg` panel with a dashed border and a "?" glyph: "We couldn't confirm your booking — the connection dropped. Press Book again to check; you will not be booked twice." The button reads "Try again". `role="status"`. |
| Error (search failed) | The refused-style panel inside the results area: "Couldn't load availability" with a "Try again" button. Earlier results are cleared, not left on screen as current. |
| Stale | No stale results are ever shown. A newer search supersedes an older one. Late responses are discarded. |
| Cancelled (lookup) | Status pill: grey outline "cancelled". The cancel button is removed from the page. |
| Confirmed (lookup) | Status pill: green "confirmed". A secondary destructive-outline "Cancel booking" button. |

## Accessibility
- Every input has a visible `<label>`. Errors are linked with `aria-describedby`. Status panels use
  `role="alert"` (refused) or `role="status"` (uncertain, success).
- Focus is always visible: a 3 px `--focus` outline with a 2 px offset. Cells are `<button>`s in DOM order, so
  Tab reaches every control.
- Grid rows are a list (`<ol>`). Each cell's accessible name is "19:00, Table 2, 4 seats, available".
