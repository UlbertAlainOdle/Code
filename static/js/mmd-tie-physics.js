(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    else root.MmdTiePhysics = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    const profiles = Object.freeze({
        stable: { label: 'Stable (Recommended)', hz: 120, iterations: 30,
            description: 'Preserves tie collisions, original mass and damping with higher numerical precision.' },
        fine: { label: 'Fine Comparison', hz: 240, iterations: 30,
            description: 'Uses a smaller simulation step to check whether jitter improves at the cost of performance.' },
        'contact-off': { label: 'Chest Collision Comparison', hz: 120, iterations: 30,
            description: 'Disables tie contact with the three chest colliders; the tie may pass through clothing.' },
        legacy: { label: 'Legacy Comparison', hz: 0, iterations: 10,
            description: 'Restores the previous tie parameters and update path as a comparison baseline.' },
    });
    const authoredBodies = new WeakMap();
    const bodyFields = ['positionDamping', 'rotationDamping', 'friction', 'weight', 'groupTarget'];

    function normalizeMode(mode) {
        return Object.prototype.hasOwnProperty.call(profiles, mode) ? mode : 'stable';
    }

    function getProfile(mode) {
        const id = normalizeMode(mode);
        return { id, ...profiles[id], unitStep: profiles[id].hz ? 1 / profiles[id].hz : 1 / 65,
            // MMD uses a different length scale. Preserve the gravity actually used before this repair.
            gravity: [0, -98, 0], maxStepNum: 3 };
    }

    function isTie(body) {
        return body.type !== 0 && /^Tie_\d+$/i.test(String(body.name || ''));
    }

    function isSupport(body) {
        return body.type === 0 && /^ネクタイ用\d*$/.test(String(body.name || ''));
    }

    function tuneRigidBodies(bodies, mode) {
        const profile = getProfile(mode);
        const ties = bodies.filter(isTie);
        const supports = bodies.filter(isSupport);
        [...ties, ...supports].forEach(body => {
            if (!authoredBodies.has(body)) {
                authoredBodies.set(body, Object.fromEntries(bodyFields.map(key => [key, body[key]])));
            }
            Object.assign(body, authoredBodies.get(body));
        });

        if (profile.id === 'legacy') {
            // Reproduce the old override, including its accidental match of the support bodies.
            [...ties, ...supports].forEach(body => {
                body.positionDamping = 0.995;
                body.rotationDamping = 0.995;
                body.friction = 0.9;
            });
            // The old code wrote `mass`, which MMDPhysics never reads. Do not change `weight`.
        } else if (profile.id === 'contact-off') {
            const tieGroupMask = ties.reduce((mask, body) => mask | (1 << body.groupIndex), 0);
            // Filter on the three supports, not on the ties: hands share the supports' group.
            supports.forEach(body => { body.groupTarget &= ~tieGroupMask; });
        }

        return { mode: profile.id, tieCount: ties.length, supportCount: supports.length,
            supports: supports.map(body => ({ name: body.name, groupTarget: body.groupTarget })),
            bodies: ties.map(body => ({ name: body.name, weight: body.weight,
                positionDamping: body.positionDamping, rotationDamping: body.rotationDamping,
                friction: body.friction, groupTarget: body.groupTarget })) };
    }

    function configurePhysics(physics, mode) {
        const profile = getProfile(mode);
        const solverInfo = physics.world.getSolverInfo?.();
        const iterationsSupported = typeof solverInfo?.set_m_numIterations === 'function';
        if (iterationsSupported) solverInfo.set_m_numIterations(profile.iterations);
        return { mode: profile.id, unitStep: physics.unitStep, maxStepNum: physics.maxStepNum,
            gravity: physics.gravity.toArray(), iterationsSupported,
            solverIterations: solverInfo?.get_m_numIterations?.() ?? null };
    }

    function createStepper(mode) {
        const profile = getProfile(mode);
        let accumulator = 0;
        const stats = { steps: 0, droppedSeconds: 0 };
        return {
            stats,
            reset() { accumulator = 0; },
            advance(elapsed, update) {
                if (!Number.isFinite(elapsed) || elapsed <= 0) return 0;
                if (!profile.hz) { update(elapsed); stats.steps++; return 1; }
                // A resumed/background tab must not inject a large jump into animated colliders.
                if (elapsed >= 0.25) {
                    stats.droppedSeconds += elapsed + accumulator;
                    accumulator = 0;
                    return 0;
                }
                const maxSteps = profile.hz / 15;
                const budget = maxSteps * profile.unitStep;
                const accepted = Math.min(elapsed, budget);
                stats.droppedSeconds += elapsed - accepted;
                accumulator += accepted;
                const steps = Math.min(maxSteps, Math.floor((accumulator + 1e-10) / profile.unitStep));
                accumulator = Math.max(0, accumulator - steps * profile.unitStep);
                for (let i = 0; i < steps; i++) update(profile.unitStep);
                stats.steps += steps;
                return steps;
            },
        };
    }

    return { profiles, normalizeMode, getProfile, tuneRigidBodies, configurePhysics, createStepper };
});
