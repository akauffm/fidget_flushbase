// Shared hydraulic model for FlushTracker.
//
// Loaded by BOTH flushtracker.html and dashboard.html so the two pages can
// never disagree about where a flush is. Everything downstream is derived from
// the route geometry plus two velocities — there is no stored position, only
// elapsed time since the flush.
//
// Load order matters: this file must come before the page's own <script> block.

// Geographic path: 1417 15th St -> SFPUC collection system -> SEP -> Pier 80 outfall
const sewerPathCoordinates = [
    [37.7666712, -122.4168153], // 1. 1417 15th St (Origin)
    [37.7666118, -122.4176682], // 2. 15th St lateral segment
    [37.7481885, -122.4159071], // 3. Mission / Bernal flow corridor
    [37.7482562, -122.4083325], // 4. Channel Basin trunk conduit
    [37.7495458, -122.4035045], // 5. Islais Creek basin corridor
    [37.7496816, -122.3972817], // 6. Evans Ave approach
    [37.7472386, -122.3961013], // 7. Phelps St headworks entrance
    [37.7431662, -122.3886129], // 8. Southeast Wastewater Treatment Plant (SEP)
    [37.75037559393314, -122.37329860682091] // 9. SF Bay Deepwater Outfall (800 ft offshore near Pier 80)
];

// Index into sewerPathCoordinates marking where the plant sits. Everything
// before it is pipe; everything after it is the outfall run.
const SEP_COORD_INDEX = 7;

// --- Geometry ---

function distanceFt(a, b) {
    const R = 20902231; // Earth radius in feet
    const toRad = (d) => d * Math.PI / 180;
    const dLat = toRad(b[0] - a[0]);
    const dLng = toRad(b[1] - a[1]);
    const h = Math.sin(dLat / 2) ** 2 +
        Math.cos(toRad(a[0])) * Math.cos(toRad(b[0])) * Math.sin(dLng / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(h));
}

function pathDistanceFt(points) {
    let total = 0;
    for (let i = 0; i < points.length - 1; i++) total += distanceFt(points[i], points[i + 1]);
    return total;
}

// --- Hydraulic timing ---
// Distances come from the route geometry itself; velocities set the clock.

const LATERAL_FT_PER_SEC = 2.5;  // 4-inch private lateral (gravity flow)
const MAINS_FT_PER_SEC = 8.0;    // Street mains & trunk lines

const LATERAL_FT = distanceFt(sewerPathCoordinates[0], sewerPathCoordinates[1]);          // ~250 ft
const MAINS_FT = pathDistanceFt(sewerPathCoordinates.slice(1, SEP_COORD_INDEX + 1));      // ~16,000 ft to SEP
const LATERAL_SEC = LATERAL_FT / LATERAL_FT_PER_SEC;                                      // ~100 s
const SEP_ARRIVAL_SEC = LATERAL_SEC + MAINS_FT / MAINS_FT_PER_SEC;                        // ~35 min
const TREATMENT_SEC = 24 * 3600;                                                          // 24 hr liquid cycle
const DISCHARGE_SEC = SEP_ARRIVAL_SEC + TREATMENT_SEC;

// Progress-bar breakpoints, so the bar and the stepper always agree
const PROGRESS_AT_MAINS = 10;      // % reached when the lateral is done
const PROGRESS_AT_SEP = 68;        // % reached on arrival at the plant
const PROGRESS_AT_DISCHARGE = 95;  // % reached when treatment completes

// The four journey phases, in order. Index doubles as the stepper node index.
const FLUSH_PHASES = [
    { key: 'lateral', label: 'IN LATERAL' },
    { key: 'mains', label: 'IN TRANSIT' },
    { key: 'treatment', label: 'AT SEP PLANT' },
    { key: 'discharged', label: 'DISCHARGED' }
];

function elapsedSecSince(timestampMs) {
    return Math.max(0, (Date.now() - Number(timestampMs)) / 1000);
}

function flushPhaseIndex(elapsedSec) {
    if (elapsedSec < LATERAL_SEC) return 0;
    if (elapsedSec < SEP_ARRIVAL_SEC) return 1;
    if (elapsedSec < DISCHARGE_SEC) return 2;
    return 3;
}

function flushPhase(elapsedSec) {
    return FLUSH_PHASES[flushPhaseIndex(elapsedSec)];
}

// Where a flush is at `elapsedSec`, as both a phase and a progress percentage.
// `traveledFt` is the distance covered along the current phase's sub-path
// (meaningless for the treatment/discharged phases, which are time-based).
function flushProgress(elapsedSec) {
    const phaseIndex = flushPhaseIndex(elapsedSec);

    if (phaseIndex === 0) {
        return {
            phaseIndex,
            traveledFt: elapsedSec * LATERAL_FT_PER_SEC,
            progressPercent: (elapsedSec / LATERAL_SEC) * PROGRESS_AT_MAINS
        };
    }
    if (phaseIndex === 1) {
        const inPhase = elapsedSec - LATERAL_SEC;
        const span = SEP_ARRIVAL_SEC - LATERAL_SEC;
        return {
            phaseIndex,
            traveledFt: inPhase * MAINS_FT_PER_SEC,
            progressPercent: PROGRESS_AT_MAINS + (inPhase / span) * (PROGRESS_AT_SEP - PROGRESS_AT_MAINS)
        };
    }
    if (phaseIndex === 2) {
        const ratio = (elapsedSec - SEP_ARRIVAL_SEC) / TREATMENT_SEC;
        return {
            phaseIndex,
            traveledFt: 0,
            progressPercent: PROGRESS_AT_SEP + ratio * (PROGRESS_AT_DISCHARGE - PROGRESS_AT_SEP)
        };
    }
    return { phaseIndex, traveledFt: 0, progressPercent: 100 };
}
