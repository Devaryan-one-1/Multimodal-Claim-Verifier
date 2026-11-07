# Customer phone layout acceptance

Item 9 in [BETA_TODO.md](BETA_TODO.md). Implemented with `gsd-debug` inline; the user reports the updated layout works. Detailed keyboard/theme and native-runtime checks remain open.

User acceptance: "works" after the updated-layout check. This confirms general layout behavior; exact viewport dimensions, zoom range, keyboard and theme results were not separately reported.

The customer app supports a minimum content width of **375 CSS pixels**, matching iPhone X portrait. A narrower viewport retains that minimum and can scroll horizontally. Phone styling applies through 767px; desktop styling starts at 768px.

The agreed phone layout has Reviews / Documents / History along the bottom, Settings at the top right, primary actions at full width below their forms, and the cited PDF below the claim/calculation. Settings holds Workspace, the existing OpenRouter key/models controls and Sign out on both phone and desktop. Desktop keeps top navigation and side-by-side results/PDF. There is one set of native widgets; resizing does not switch to duplicate forms. Model and claim drafts retain their existing session/workspace rules.

PDF preview defaults to 100% (fit). Higher zoom enlarges the page within its own horizontal scroll region. Swipe to pan or Tab to the preview and use arrow keys. Source/version/membership checks and bounded rasterization remain in place; no shared media URL is created.

## Check the updated app

Restart `streamlit run app/customer_main.py` in the usual project environment, or rebuild/recreate the customer services if using Docker. Keep the existing worker running for processing. No new upload or index rebuild is needed for this check; use an existing ready report and completed review.

Use browser responsive mode for the dimensions below. Device emulation alone does not prove behavior on an actual iPhone.

| Check | Expected | Result |
|---|---|---|
| 375 × 812 and 390px wide | Main page fits without sideways scrolling. Bottom navigation is visible; final Save/export controls can scroll fully above it. Settings opens within the screen and can scroll internally. Phone action buttons remain below their forms at full width. | General layout user-reported working; exact dimensions/safe-area/final-control checks not separately recorded |
| Cited PDF on phone | Claim/calculation and review form come first; PDF follows below. 100% fits; 175–200% visibly enlarges the page and pans within the preview. Cited numbers remain readable. | General layout user-reported working; zoom range/readability not separately confirmed |
| Resize 767 → 768 → 1280px and back | Navigation moves between bottom and top. Results/PDF stack on phone and appear side by side on desktop. No duplicate controls or widget errors. | Not run |
| Unsaved claim, review note and model edit | After leaving the field, resize/navigate away and back; each draft remains in its workspace. Changing workspace does not show the other workspace's draft. Sign out clears customer session drafts. | Not run |
| Keyboard | Tab reaches Settings, forms, preview, Save/export and navigation; focus is visible. Native radio arrow keys/Space change the selected area. Enter activates focused buttons. Escape closes Settings and focus remains usable. No trap or covered focused action. | Not run |
| Light and dark themes; actual iPhone if available | Navigation labels, selected state, focus and Settings are readable. Safe-area padding keeps the bottom controls above the home indicator. Keyboard opening does not strand form actions. | Not run |

Record any failure with width, page/area, theme and steps. The requested 375px minimum does not establish 320px reflow or full accessibility compliance.

## Automated evidence

Local full suite on 2026-11-03: **283 run, 276 passed, 7 skipped**, using the available Blender Python 3.11 interpreter. Ten new tests cover authenticated Settings access, identity/workspace changes, metadata revocation, one key form set, navigation keys and private/escaped/focusable zoom markup. Existing draft, numerical, export and recovery checks remain included. The seven skips require the project's native encryption installation.

`deploy/customer_ui_smoke.py` now exercises native Streamlit Settings, workspace/model drafts, navigation/claim drafts, real fixture-PDF rendering/zoom, history callbacks, inline exports and revocation. Its Python source and all embedded fixture scripts pass syntax checks; execution is **pending**. The expanded `deploy/test-docker.ps1` includes it and the new mobile regression file. Run that helper in the usual PowerShell/Docker environment. Its fixture checks spend no API credits and do not use customer data; the existing dependency audit still runs and can block release separately.

This managed session cannot start the configured project interpreter or access Docker. Browser automation also fails during kernel initialization (`os error 3`). No screenshot, actual viewport or browser keyboard acceptance is claimed from the unit tests or the prepared AppTest runner.
